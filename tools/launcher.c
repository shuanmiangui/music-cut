#include <mach-o/dyld.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

static void alert_and_exit(const char *message) {
    pid_t pid = fork();
    if (pid == 0) {
        execl("/usr/bin/osascript", "osascript", "-e",
              "display alert \"Music Cut 启动失败\" message \"请先运行 setup.command，或查看 logs/gui.log。\" as critical",
              (char *)NULL);
        _exit(127);
    }
    if (pid > 0) {
        int status = 0;
        waitpid(pid, &status, 0);
    }
    fprintf(stderr, "%s\n", message);
    exit(1);
}

int main(void) {
    char executable[PATH_MAX];
    uint32_t size = sizeof(executable);
    if (_NSGetExecutablePath(executable, &size) != 0) {
        return 1;
    }
    char resolved[PATH_MAX];
    if (realpath(executable, resolved) == NULL) {
        return 1;
    }
    // .../Music Cut.app/Contents/MacOS/MusicCut -> .../Music Cut.app
    char *slash = strrchr(resolved, '/');
    if (!slash) return 1;
    *slash = '\0'; // MacOS
    slash = strrchr(resolved, '/');
    if (!slash) return 1;
    *slash = '\0'; // Contents
    slash = strrchr(resolved, '/');
    if (!slash) return 1;
    *slash = '\0'; // .app
    slash = strrchr(resolved, '/');
    if (!slash) return 1;
    *slash = '\0'; // app root (parent of .app)

    char python[PATH_MAX];
    snprintf(python, sizeof(python), "%s/.venv/bin/python", resolved);
    if (access(python, X_OK) != 0) {
        alert_and_exit("Python environment not found");
    }
    if (chdir(resolved) != 0) {
        return 1;
    }
    char logs[PATH_MAX];
    snprintf(logs, sizeof(logs), "%s/logs", resolved);
    mkdir(logs, 0755);
    char logfile[PATH_MAX];
    snprintf(logfile, sizeof(logfile), "%s/gui.log", logs);
    FILE *log = fopen(logfile, "a");
    if (log) {
        dup2(fileno(log), STDOUT_FILENO);
        dup2(fileno(log), STDERR_FILENO);
        fclose(log);
    }
    execl(python, python, "-m", "musiccut.gui", (char *)NULL);
    alert_and_exit("Could not launch Music Cut");
    return 1;
}
