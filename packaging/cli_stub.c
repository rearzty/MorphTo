/*
 * morphto.com: console front-end for the windowed MorphTo.exe.
 *
 * Typing `morphto` in a terminal finds morphto.com before MorphTo.exe (PATHEXT order), so command-line
 * use gets a real console program: it waits for the conversion, shows its output and returns its
 * exit code. It simply runs "MorphTo.exe --cli <same arguments>" from its own folder.
 */
#include <windows.h>

static const wchar_t *skip_argv0(const wchar_t *cmd) {
    if (*cmd == L'"') {
        cmd++;
        while (*cmd && *cmd != L'"') cmd++;
        if (*cmd) cmd++;
    } else {
        while (*cmd && *cmd != L' ' && *cmd != L'\t') cmd++;
    }
    while (*cmd == L' ' || *cmd == L'\t') cmd++;
    return cmd;
}

int wmain(void) {
    wchar_t exe[MAX_PATH];
    DWORD n = GetModuleFileNameW(NULL, exe, MAX_PATH);
    if (n == 0 || n >= MAX_PATH - 4) return 1;
    wchar_t *slash = wcsrchr(exe, L'\\');
    if (!slash) return 1;
    wcscpy(slash + 1, L"MorphTo.exe");

    const wchar_t *rest = skip_argv0(GetCommandLineW());
    size_t len = wcslen(exe) + wcslen(rest) + 16;
    wchar_t *cmd = (wchar_t *)HeapAlloc(GetProcessHeap(), 0, len * sizeof(wchar_t));
    if (!cmd) return 1;
    wsprintfW(cmd, L"\"%s\" --cli %s", exe, rest);

    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof si);
    si.cb = sizeof si;
    si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
    si.hStdOutput = GetStdHandle(STD_OUTPUT_HANDLE);
    si.hStdError = GetStdHandle(STD_ERROR_HANDLE);

    if (!CreateProcessW(exe, cmd, NULL, NULL, TRUE, 0, NULL, NULL, &si, &pi)) {
        DWORD written;
        const char msg[] = "morphto: cannot start MorphTo.exe\r\n";
        WriteFile(si.hStdError, msg, sizeof msg - 1, &written, NULL);
        return 1;
    }
    /* Ctrl+C reaches the child through the shared console; we just wait for it. */
    SetConsoleCtrlHandler(NULL, TRUE);
    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD code = 1;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return (int)code;
}
