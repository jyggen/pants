/* Copyright 2021 Pants project contributors (see CONTRIBUTORS.md).
 * Licensed under the Apache License, Version 2.0 (see LICENSE).
 */

/*
 * Analyze a Go package for imports and other metadata.
 *
 * Note: `go list` can return this data but requires the full set of dependencies to be available. It is much
 * better for performance to not copy those dependencies into the input root. Hence doing this analysis here.
 *
 * Loosely based on the analysis in the go/build stdlib module.
 * See https://cs.opensource.google/go/go/+/refs/tags/go1.17.2:src/go/build/build.go;drc=refs%2Ftags%2Fgo1.17.2;l=512.
 */

package main

import (
	"encoding/json"
	"fmt"
	"go/ast"
	"go/build"
	"go/token"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// Package represents the results of analyzing a Go package.
type Package struct {
	Name    string   // package name
	AllTags []string `json:",omitempty"` // tags that can influence file selection in this directory

	// Source files
	GoFiles           []string `json:",omitempty"` // .go source files (excluding CgoFiles, TestGoFiles, XTestGoFiles)
	CgoFiles          []string `json:",omitempty"` // .go source files that import "C"
	IgnoredGoFiles    []string `json:",omitempty"` // .go source files ignored for this build (including ignored _test.go files)
	IgnoredOtherFiles []string `json:",omitempty"` // non-.go source files ignored for this build
	CFiles            []string `json:",omitempty"` // .c source files
	CXXFiles          []string `json:",omitempty"` // .cc, .cpp and .cxx source files
	MFiles            []string `json:",omitempty"` // .m (Objective-C) source files
	HFiles            []string `json:",omitempty"` // .h, .hh, .hpp and .hxx source files
	FFiles            []string `json:",omitempty"` // .f, .F, .for and .f90 Fortran source files
	SFiles            []string `json:",omitempty"` // .s source files
	SwigFiles         []string `json:",omitempty"` // .swig files
	SwigCXXFiles      []string `json:",omitempty"` // .swigcxx files
	SysoFiles         []string `json:",omitempty"` // .syso system object files to add to archive

	// Cgo directives
	CgoCFLAGS    []string `json:",omitempty"` // Cgo CFLAGS directives
	CgoCPPFLAGS  []string `json:",omitempty"` // Cgo CPPFLAGS directives
	CgoCXXFLAGS  []string `json:",omitempty"` // Cgo CXXFLAGS directives
	CgoFFLAGS    []string `json:",omitempty"` // Cgo FFLAGS directives
	CgoLDFLAGS   []string `json:",omitempty"` // Cgo LDFLAGS directives
	CgoPkgConfig []string `json:",omitempty"` // Cgo pkg-config directives

	// Test information
	TestGoFiles  []string `json:",omitempty"`
	XTestGoFiles []string `json:",omitempty"`

	// Dependency information
	// Note: This does not include the token position information for the imports.
	Imports      []string `json:",omitempty"`
	TestImports  []string `json:",omitempty"`
	XTestImports []string `json:",omitempty"`

	// //go:embed patterns found in Go source files
	// For example, if a source file says
	//	//go:embed a* b.c
	// then the list will contain those two strings as separate entries.
	// (See package embed for more details about //go:embed.)
	EmbedPatterns      []string `json:",omitempty"` // patterns from GoFiles, CgoFiles
	TestEmbedPatterns  []string `json:",omitempty"` // patterns from TestGoFiles
	XTestEmbedPatterns []string `json:",omitempty"` // patterns from XTestGoFiles

	// Error information. This differs from how `go list` reports errors.
	InvalidGoFiles map[string]string `json:",omitempty"`
	Error          string            `json:",omitempty"`
}

func analyzeFile(fileSet *token.FileSet, filename string) (*fileInfo, error) {
	fi := fileInfo{filename: filename, fset: fileSet}

	f, err := os.Open(filename)
	if err != nil {
		return &fi, err
	}
	defer f.Close()

	err = readGoInfo(f, &fi)
	if err != nil {
		return &fi, err
	}

	return &fi, nil
}

// Copied from https://cs.opensource.google/go/go/+/refs/tags/go1.17.2:src/go/build/build.go;l=1024;drc=refs%2Ftags%2Fgo1.17.2
func fileListForExt(p *Package, ext string) *[]string {
	switch ext {
	case ".c":
		return &p.CFiles
	case ".cc", ".cpp", ".cxx":
		return &p.CXXFiles
	case ".m":
		return &p.MFiles
	case ".h", ".hh", ".hpp", ".hxx":
		return &p.HFiles
	case ".f", ".F", ".for", ".f90":
		return &p.FFiles
	case ".s", ".S", ".sx":
		return &p.SFiles
	case ".swig":
		return &p.SwigFiles
	case ".swigcxx":
		return &p.SwigCXXFiles
	case ".syso":
		return &p.SysoFiles
	}
	return nil
}

func cleanStringSet(valuesMap map[string]bool) []string {
	var values []string
	for value, _ := range valuesMap {
		values = append(values, value)
	}
	sort.Strings(values)
	return values
}

// saveCgo saves the information from the #cgo lines in the import "C" comment.
// These lines set CFLAGS, CPPFLAGS, CXXFLAGS and LDFLAGS and pkg-config directives
// that affect the way cgo's C code is built.
func saveCgo(filename string, pkg *Package, cg *ast.CommentGroup, buildContext *build.Context) error {
	text := cg.Text()
	for _, line := range strings.Split(text, "\n") {
		orig := line

		// Line is
		//      #cgo [GOOS/GOARCH...] LDFLAGS: stuff
		//
		line = strings.TrimSpace(line)
		if len(line) < 5 || line[:4] != "#cgo" || (line[4] != ' ' && line[4] != '\t') {
			continue
		}

		// Split at colon.
		line, argstr, ok := stringsCut(strings.TrimSpace(line[4:]), ":")
		if !ok {
			return fmt.Errorf("%s: invalid #cgo line: %s", filename, orig)
		}

		// Parse GOOS/GOARCH stuff.
		f := strings.Fields(line)
		if len(f) < 1 {
			return fmt.Errorf("%s: invalid #cgo line: %s", filename, orig)
		}

		cond, verb := f[:len(f)-1], f[len(f)-1]
		if len(cond) > 0 {
			ok := false
			for _, c := range cond {
				if matchAuto(buildContext, c, nil) {
					ok = true
					break
				}
			}
			if !ok {
				continue
			}
		}
		args, err := splitQuoted(argstr)
		if err != nil {
			return fmt.Errorf("%s: invalid #cgo line: %s", filename, orig)
		}
		// PANTS NOTE: In the original Go code, this code would have expanded `${SRCDIR}` in a path
		// to the absolute directory where the cgo file was located. Given Pants uses an execution sandbox,
		// this will be done when actually building.
		//for i, arg := range args {
		//	if arg, ok = expandSrcDir(arg, di.Dir); !ok {
		//		return fmt.Errorf("%s: malformed #cgo argument: %s", filename, arg)
		//	}
		//	args[i] = arg
		//}

		// PANTS NOTE: In the original Go code, there was code to expanded paths passed to -I and -L compiler
		// options to be absolute path. Given Pants uses an execution sandbox, this will be done when actually building.

		switch verb {
		case "CFLAGS":
			pkg.CgoCFLAGS = append(pkg.CgoCFLAGS, args...)
		case "CPPFLAGS":
			pkg.CgoCPPFLAGS = append(pkg.CgoCPPFLAGS, args...)
		case "CXXFLAGS":
			pkg.CgoCXXFLAGS = append(pkg.CgoCXXFLAGS, args...)
		case "FFLAGS":
			pkg.CgoFFLAGS = append(pkg.CgoFFLAGS, args...)
		case "LDFLAGS":
			pkg.CgoLDFLAGS = append(pkg.CgoLDFLAGS, args...)
		case "pkg-config":
			pkg.CgoPkgConfig = append(pkg.CgoPkgConfig, args...)
		default:
			return fmt.Errorf("%s: invalid #cgo verb: %s", filename, orig)
		}
	}
	return nil
}

func analyzePackage(directory string, buildContext *build.Context) (*Package, error) {
	pkg := &Package{
		InvalidGoFiles: make(map[string]string),
	}

	fileSet := token.NewFileSet()

	entries, err := os.ReadDir(directory)
	if err != nil {
		return pkg, fmt.Errorf("failed to read directory %s: %s", directory, err)
	}

	// Keep track of the names used in `package` directives to ensure that only one package name is used.
	packageNames := make(map[string]bool)

	importsMap := make(map[string]bool)
	testImportsMap := make(map[string]bool)
	xtestImportsMap := make(map[string]bool)

	embedsMap := make(map[string]bool)
	testEmbedsMap := make(map[string]bool)
	xtestEmbedsMap := make(map[string]bool)

	allTags := make(map[string]bool)

	var cgoSfiles []string // files with ".S"(capital S)/.sx(capital s equivalent for case insensitive filesystems)

	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}

		name := entry.Name()
		ext := filepath.Ext(name)

		if entry.Type()&fs.ModeSymlink != 0 {
			linkFullPath := filepath.Join(directory, name)
			linkStat, err := os.Stat(linkFullPath)
			if err != nil {
				// TODO: Report this error?
				continue
			}
			if linkStat.IsDir() {
				continue
			}
		}

		// TODO: `MatchFile` will actually parse the imports but does not return the AST. Consider vendoring
		// the MatchFile logic to avoid double parsing.
		binaryOnly := false
		fileInfo, err := matchFile(buildContext, directory, name, allTags, &binaryOnly, fileSet)
		if err != nil {
			pkg.InvalidGoFiles[name] = err.Error()
			continue
		}
		if fileInfo == nil {
			if strings.HasPrefix(name, "_") || strings.HasPrefix(name, ".") {
				// `go` ignores files prefixed with underscore or period. Since this is not due to
				// build constraints, do not report it as an ignored file. Fall through.
			} else if ext == ".go" {
				pkg.IgnoredGoFiles = append(pkg.IgnoredGoFiles, name)
			} else if fileListForExt(pkg, ext) != nil {
				pkg.IgnoredOtherFiles = append(pkg.IgnoredOtherFiles, name)
			}
			continue
		}

		// Going to save the file. For non-Go files, can stop here.
		switch ext {
		case ".go":
			// keep going
		case ".S", ".sx":
			// special case for cgo, handled at end
			cgoSfiles = append(cgoSfiles, name)
			continue
		default:
			if list := fileListForExt(pkg, ext); list != nil {
				*list = append(*list, name)
			}
			continue
		}

		analysis, err := analyzeFile(fileSet, filepath.Join(directory, name))
		if err != nil {
			pkg.InvalidGoFiles[name] = err.Error()
			// Fall-through to allow still listing the file's existence.
			// TODO: This should just be I/O errors now, so consider erroring out here as that is infra failure.
		}
		if analysis.parseErr != nil {
			pkg.InvalidGoFiles[name] = analysis.parseErr.Error()
			// Fall-through to allow still listing the file's existence.
		}

		var pkgName string
		if analysis != nil {
			pkgName = analysis.pkg
			if pkgName == "documentation" {
				// Ignore package documentation that are in `documentation` package.
				pkg.IgnoredGoFiles = append(pkg.IgnoredGoFiles, name)
				continue
			}
		}

		isTest := strings.HasSuffix(name, "_test.go")
		isXTest := false
		if isTest && strings.HasSuffix(pkgName, "_test") {
			isXTest = true
			pkgName = pkgName[:len(pkgName)-len("_test")]
		}
		packageNames[pkgName] = true

		// TODO: Handle import comments?
		// See https://cs.opensource.google/go/go/+/refs/tags/go1.17.2:src/go/build/build.go;drc=refs%2Ftags%2Fgo1.17.2;l=920

		// Check whether CGo is in use.
		isCGo := false
		if analysis != nil {
			for _, imp := range analysis.imports {
				if imp.path == "C" {
					if isTest {
						pkg.InvalidGoFiles[name] = fmt.Sprintf("use of cgo in test %s not supported", name)
						continue
					}
					isCGo = true
					if err := saveCgo(name, pkg, imp.doc, buildContext); err != nil {
						pkg.InvalidGoFiles[name] = fmt.Sprintf("cgo error: %s", err)
						continue
					}
				}
			}
		}

		var fileList *[]string
		var embedsMapForFile map[string]bool
		var importsMapForFile map[string]bool

		switch {
		case isCGo:
			allTags["cgo"] = true
			if buildContext.CgoEnabled {
				fileList = &pkg.CgoFiles
				importsMapForFile = importsMap
				embedsMapForFile = embedsMap
			} else {
				// Ignore imports and embeds from cgo files if cgo is disabled.
				fileList = &pkg.IgnoredGoFiles
			}
		case isXTest:
			fileList = &pkg.XTestGoFiles
			importsMapForFile = xtestImportsMap
			embedsMapForFile = xtestEmbedsMap
		case isTest:
			fileList = &pkg.TestGoFiles
			importsMapForFile = testImportsMap
			embedsMapForFile = testEmbedsMap
		default:
			fileList = &pkg.GoFiles
			importsMapForFile = importsMap
			embedsMapForFile = embedsMap
		}

		*fileList = append(*fileList, name)

		if importsMapForFile != nil && analysis != nil {
			for _, importPath := range analysis.imports {
				importsMapForFile[importPath.path] = true
			}
		}

		if embedsMapForFile != nil && len(analysis.embeds) > 0 {
			for _, e := range analysis.embeds {
				embedsMapForFile[e.pattern] = true
			}
		}
	}

	pkg.AllTags = cleanStringSet(allTags)

	pkg.Imports = cleanStringSet(importsMap)
	pkg.TestImports = cleanStringSet(testImportsMap)
	pkg.XTestImports = cleanStringSet(xtestImportsMap)

	pkg.EmbedPatterns = cleanStringSet(embedsMap)
	pkg.TestEmbedPatterns = cleanStringSet(testEmbedsMap)
	pkg.XTestEmbedPatterns = cleanStringSet(xtestEmbedsMap)

	// add the .S/.sx files only if we are using cgo
	// (which means gcc will compile them).
	// The standard assemblers expect .s files.
	if len(pkg.CgoFiles) > 0 {
		pkg.SFiles = append(pkg.SFiles, cgoSfiles...)
		sort.Strings(pkg.SFiles)
	} else {
		pkg.IgnoredOtherFiles = append(pkg.IgnoredOtherFiles, cgoSfiles...)
		sort.Strings(pkg.IgnoredOtherFiles)
	}

	// Set the package name from the observed package name. "There can be only one."
	var packageNamesList []string
	for pn, _ := range packageNames {
		packageNamesList = append(packageNamesList, pn)
	}
	if len(packageNamesList) == 1 {
		pkg.Name = packageNamesList[0]
	} else if len(packageNamesList) > 1 {
		return pkg, fmt.Errorf("multiple package names encountered: %s", strings.Join(packageNamesList, ", "))
	}

	if len(pkg.GoFiles)+len(pkg.CgoFiles)+len(pkg.TestGoFiles)+len(pkg.XTestGoFiles) == 0 {
		return pkg, fmt.Errorf("no buildable Go source files in %s", directory)
	}

	return pkg, nil
}

func main() {
	// TODO: Consider allowing caller to set build tags or platform? Setting platform GOOS/GOARCH will be
	// necessary for multi-platform support.
	buildContext := &build.Default

	for _, arg := range os.Args[1:] {
		pkg, err := analyzePackage(arg, buildContext)
		if err != nil {
			pkg.Error = err.Error()
		}
		if pkg.Error == "" && len(pkg.InvalidGoFiles) > 0 {
			pkg.Error = "invalid Go sources encountered"
		}

		outputBytes, err := json.Marshal(pkg)
		if err != nil {
			fmt.Printf("{\"Error\": \"Failed to encode package metadata: %s\"}", err)
			continue
		}
		_, err = os.Stdout.Write(outputBytes)
		if err != nil {
			fmt.Printf("{\"Error\": \"Failed to write package metadata: %s\"}", err)
			continue
		}
	}

	os.Exit(0)
}
