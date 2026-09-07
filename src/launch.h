#ifndef DARKLANTERN_LAUNCH_H
#define DARKLANTERN_LAUNCH_H

#include "game.h"

#define DL_MAX_BUNDLE_LEVELS 8
#define DL_MAX_LEVEL_STARTS 16

typedef struct {
    const char *id, *title;
    const DlLevel *(*load)(void);
} DlLevelEntry;

typedef struct {
    const char *title;
    const DlLevelEntry *levels;
    int level_count;
    int initial_level, initial_start; /* -1 start means the ordinary spawn. */
    bool start_in_menu, has_rom_assets;
} DlBundle;

typedef struct {
    const DlBundle *bundle;
    bool menu_open, has_active;
    int selected_level, selected_start, active_level, active_start;
} DlLauncher;

typedef struct {
    int level_delta, start_delta;
    bool accept, back, open_menu;
} DlLaunchInput;

typedef enum {
    DL_LAUNCH_NONE, DL_LAUNCH_LEVEL, DL_LAUNCH_RESUME, DL_LAUNCH_MENU_OPENED
} DlLaunchAction;

/* Validates descriptors before use. Invalid initial choices safely select the
 * first level/default spawn. Loaders only return immutable compiled data. */
bool dl_launcher_init(DlLauncher *launcher, const DlBundle *bundle);
DlLaunchAction dl_launcher_update(DlLauncher *launcher, DlLaunchInput input);
const DlLevel *dl_launcher_selected_level(const DlLauncher *launcher);
const DlLevel *dl_launcher_active_level(const DlLauncher *launcher);
const char *dl_launcher_start_label(const DlLauncher *launcher);

#endif
