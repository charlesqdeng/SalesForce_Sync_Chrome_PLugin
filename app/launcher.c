// App bundle executable: opens Terminal and runs start.sh from the project
// root, three levels above Contents/MacOS/launcher.
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(void) {
    char exe[PATH_MAX], resolved[PATH_MAX], script[PATH_MAX];
    uint32_t size = sizeof(exe);
    if (_NSGetExecutablePath(exe, &size) != 0 || !realpath(exe, resolved)) {
        return 1;
    }
    for (int i = 0; i < 4; i++) {
        char *slash = strrchr(resolved, '/');
        if (!slash) {
            return 1;
        }
        *slash = '\0';
    }
    snprintf(script, sizeof(script), "%s/start.sh", resolved);
    execl("/usr/bin/open", "open", "-a", "Terminal", script, (char *)NULL);
    return 1;
}
