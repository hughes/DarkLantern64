#include "launch.h"
#include <string.h>

static int wrap(int value, int delta, int count) {
    int next = (value + delta % count) % count;
    return next < 0 ? next + count : next;
}

bool dl_launcher_init(DlLauncher *launcher, const DlBundle *bundle) {
    if (!launcher) return false;
    memset(launcher, 0, sizeof(*launcher));
    launcher->active_level = launcher->active_start = launcher->selected_start = -1;
    if (!bundle || !bundle->levels || bundle->level_count < 1 ||
        bundle->level_count > DL_MAX_BUNDLE_LEVELS) return false;
    for (int i = 0; i < bundle->level_count; ++i) {
        const DlLevelEntry *entry = &bundle->levels[i];
        if (!entry->id || !entry->title || !entry->load) return false;
        const DlLevel *level = entry->load();
        if (!level || level->test_start_count < 0 || level->test_start_count > DL_MAX_LEVEL_STARTS ||
            (level->test_start_count && !level->test_starts)) return false;
    }
    launcher->bundle = bundle;
    bool valid_initial = bundle->initial_level >= 0 && bundle->initial_level < bundle->level_count;
    launcher->selected_level = valid_initial ? bundle->initial_level : 0;
    const DlLevel *level = dl_launcher_selected_level(launcher);
    if (valid_initial && bundle->initial_start >= 0 && bundle->initial_start < level->test_start_count)
        launcher->selected_start = bundle->initial_start;
    launcher->menu_open = bundle->start_in_menu;
    if (!launcher->menu_open) {
        launcher->active_level = launcher->selected_level;
        launcher->active_start = launcher->selected_start;
        launcher->has_active = true;
    }
    return true;
}

const DlLevel *dl_launcher_selected_level(const DlLauncher *launcher) {
    if (!launcher || !launcher->bundle) return NULL;
    return launcher->bundle->levels[launcher->selected_level].load();
}

const DlLevel *dl_launcher_active_level(const DlLauncher *launcher) {
    if (!launcher || !launcher->bundle || !launcher->has_active) return NULL;
    return launcher->bundle->levels[launcher->active_level].load();
}

const char *dl_launcher_start_label(const DlLauncher *launcher) {
    const DlLevel *level = dl_launcher_selected_level(launcher);
    if (!level || launcher->selected_start < 0) return "Default spawn";
    const DlStartPreset *start = &level->test_starts[launcher->selected_start];
    return start->label ? start->label : start->id;
}

DlLaunchAction dl_launcher_update(DlLauncher *launcher, DlLaunchInput input) {
    if (!launcher || !launcher->bundle) return DL_LAUNCH_NONE;
    if (!launcher->menu_open) {
        if (!input.open_menu) return DL_LAUNCH_NONE;
        launcher->selected_level = launcher->active_level;
        launcher->selected_start = launcher->active_start;
        launcher->menu_open = true;
        return DL_LAUNCH_MENU_OPENED;
    }
    if (input.back && launcher->has_active) {
        launcher->menu_open = false;
        return DL_LAUNCH_RESUME;
    }
    if (input.level_delta) {
        launcher->selected_level = wrap(launcher->selected_level, input.level_delta,
                                        launcher->bundle->level_count);
        launcher->selected_start = -1;
    }
    const DlLevel *level = dl_launcher_selected_level(launcher);
    if (input.start_delta)
        launcher->selected_start = wrap(launcher->selected_start + 1,
            input.start_delta, level->test_start_count + 1) - 1;
    if (!input.accept) return DL_LAUNCH_NONE;
    launcher->active_level = launcher->selected_level;
    launcher->active_start = launcher->selected_start;
    launcher->has_active = true;
    launcher->menu_open = false;
    return DL_LAUNCH_LEVEL;
}
