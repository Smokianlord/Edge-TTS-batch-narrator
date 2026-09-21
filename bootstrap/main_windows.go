//go:build windows

package main

import (
	"embed"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"time"
	"unsafe"
)

const (
	appName        = "Edge TTS batch narrator"
	appVersion     = "1.0.0"
	pythonVersion  = "3.12.10"
	pythonURL      = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
	createNoWindow = 0x08000000
)

//go:embed payload/**
var payload embed.FS

func main() {
	root, err := runtimeRoot()
	if err != nil {
		fatal(err)
		return
	}
	if err := installPayload(root); err != nil {
		fatal(fmt.Errorf("could not prepare application files: %w", err))
		return
	}

	py, err := findUsablePython()
	if err != nil || py == "" {
		info("First launch setup", appName+" needs a local Python runtime for its TTS engine.\n\nThe official Python runtime will now be installed for your Windows user account. This is a one-time setup and may take a few minutes.")
		py, err = installPython(root)
		if err != nil {
			fatal(err)
			return
		}
	}

	venvPython, err := ensureVenv(root, py)
	if err != nil {
		fatal(err)
		return
	}
	if err := ensureDependencies(root, venvPython); err != nil {
		fatal(err)
		return
	}
	if err := launchApp(root); err != nil {
		fatal(err)
		return
	}
}

func runtimeRoot() (string, error) {
	base := os.Getenv("LOCALAPPDATA")
	if base == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return "", err
		}
		base = filepath.Join(home, "AppData", "Local")
	}
	root := filepath.Join(base, "EdgeTTSBatchNarrator", appVersion)
	if err := os.MkdirAll(root, 0755); err != nil {
		return "", err
	}
	return root, nil
}

func installPayload(root string) error {
	files := []string{
		"payload/src/app.py",
		"payload/src/backend.py",
		"payload/assets/app.ico",
		"payload/assets/app.png",
		"payload/requirements.txt",
	}
	for _, name := range files {
		b, err := payload.ReadFile(name)
		if err != nil {
			return err
		}
		rel := strings.TrimPrefix(name, "payload/")
		dst := filepath.Join(root, filepath.FromSlash(rel))
		if err := os.MkdirAll(filepath.Dir(dst), 0755); err != nil {
			return err
		}
		if err := os.WriteFile(dst, b, 0644); err != nil {
			return err
		}
	}
	return nil
}

func findUsablePython() (string, error) {
	candidates := [][]string{
		{"py", "-3.12"},
		{"py", "-3"},
		{"python"},
	}
	local := os.Getenv("LOCALAPPDATA")
	direct := []string{}
	if local != "" {
		direct = append(direct,
			filepath.Join(local, "Programs", "Python", "Python312", "python.exe"),
			filepath.Join(local, "Programs", "Python", "Python313", "python.exe"),
		)
	}
	for _, exePath := range direct {
		if _, err := os.Stat(exePath); err == nil && validatePython(exePath, nil) {
			return exePath, nil
		}
	}
	for _, candidate := range candidates {
		exeName := candidate[0]
		if _, err := exec.LookPath(exeName); err != nil {
			continue
		}
		args := append([]string{}, candidate[1:]...)
		args = append(args, "-c", "import sys, tkinter; assert sys.version_info >= (3,10); print(sys.executable)")
		out, err := runCapture(exeName, args...)
		if err == nil {
			path := strings.TrimSpace(out)
			if path != "" && validatePython(path, nil) {
				return path, nil
			}
		}
	}
	return "", fmt.Errorf("a compatible Python runtime was not found")
}

func validatePython(path string, prefix []string) bool {
	args := []string{"-c", "import sys, tkinter; assert sys.version_info >= (3,10)"}
	if prefix != nil {
		args = append(prefix, args...)
	}
	cmd := exec.Command(path, args...)
	hide(cmd)
	return cmd.Run() == nil
}

func installPython(root string) (string, error) {
	installer := filepath.Join(root, "python-installer.exe")
	if err := downloadFile(pythonURL, installer); err != nil {
		return "", fmt.Errorf("could not download the official Python %s installer: %w", pythonVersion, err)
	}
	args := []string{
		"/quiet",
		"InstallAllUsers=0",
		"PrependPath=0",
		"Include_launcher=1",
		"Include_pip=1",
		"Include_tcltk=1",
		"Include_test=0",
		"Include_doc=0",
		"Shortcuts=0",
	}
	cmd := exec.Command(installer, args...)
	hide(cmd)
	if err := cmd.Run(); err != nil {
		return "", fmt.Errorf("Python installation failed: %w", err)
	}
	_ = os.Remove(installer)
	local := os.Getenv("LOCALAPPDATA")
	expected := filepath.Join(local, "Programs", "Python", "Python312", "python.exe")
	for i := 0; i < 20; i++ {
		if validatePython(expected, nil) {
			return expected, nil
		}
		time.Sleep(500 * time.Millisecond)
	}
	py, err := findUsablePython()
	if err != nil || py == "" {
		return "", fmt.Errorf("Python was installed but could not be located afterwards")
	}
	return py, nil
}

func ensureVenv(root, python string) (string, error) {
	venvRoot := filepath.Join(root, ".venv")
	venvPython := filepath.Join(venvRoot, "Scripts", "python.exe")
	if _, err := os.Stat(venvPython); err == nil {
		return venvPython, nil
	}
	info("Preparing runtime", appName+" is preparing its private runtime and dependencies.\n\nThis happens only on the first launch and may take a few minutes.")
	cmd := exec.Command(python, "-m", "venv", venvRoot)
	hide(cmd)
	if err := cmd.Run(); err != nil {
		return "", fmt.Errorf("could not create the private Python environment: %w", err)
	}
	return venvPython, nil
}

func ensureDependencies(root, venvPython string) error {
	marker := filepath.Join(root, ".deps-1.0.0-ready")
	if _, err := os.Stat(marker); err == nil {
		return nil
	}
	req := filepath.Join(root, "requirements.txt")
	cmd := exec.Command(venvPython, "-m", "pip", "install", "--disable-pip-version-check", "--upgrade", "pip")
	hide(cmd)
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("could not update pip: %w", err)
	}
	cmd = exec.Command(venvPython, "-m", "pip", "install", "--disable-pip-version-check", "-r", req)
	hide(cmd)
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("could not install application dependencies. Check your internet connection and try again: %w", err)
	}
	return os.WriteFile(marker, []byte("ok\n"), 0644)
}

func launchApp(root string) error {
	pythonw := filepath.Join(root, ".venv", "Scripts", "pythonw.exe")
	app := filepath.Join(root, "src", "app.py")
	cmd := exec.Command(pythonw, app)
	cmd.Dir = root
	hide(cmd)
	if err := cmd.Start(); err != nil {
		return fmt.Errorf("could not launch %s: %w", appName, err)
	}
	return nil
}

func downloadFile(url, destination string) error {
	client := &http.Client{Timeout: 15 * time.Minute}
	resp, err := client.Get(url)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("download returned HTTP %s", resp.Status)
	}
	f, err := os.Create(destination)
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = io.Copy(f, resp.Body)
	return err
}

func runCapture(name string, args ...string) (string, error) {
	cmd := exec.Command(name, args...)
	hide(cmd)
	out, err := cmd.Output()
	return string(out), err
}

func hide(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: createNoWindow}
}

func info(title, message string) {
	messageBox(title, message, 0x00000040)
}

func fatal(err error) {
	messageBox(appName+" - Error", err.Error(), 0x00000010)
}

func messageBox(title, message string, icon uintptr) {
	user32 := syscall.NewLazyDLL("user32.dll")
	proc := user32.NewProc("MessageBoxW")
	titlePtr, _ := syscall.UTF16PtrFromString(title)
	msgPtr, _ := syscall.UTF16PtrFromString(message)
	proc.Call(0, uintptr(unsafe.Pointer(msgPtr)), uintptr(unsafe.Pointer(titlePtr)), icon)
}
