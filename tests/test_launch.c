#include "launch.h"
#include <assert.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>

static const DlStartPreset starts[] = {
    {.id="door", .label="At the door"}, {.id="roof", .label="Rooftop"}
};
static const DlLevel plain = {.title="Plain"};
static const DlLevel textured = {.title="Textured",.test_starts=starts,.test_start_count=2};
static const DlLevel *load_plain(void) { return &plain; }
static const DlLevel *load_textured(void) { return &textured; }
static const DlLevel *load_null(void) { return NULL; }
static DlLevel malformed;
static const DlLevel *load_malformed(void) { return &malformed; }
static const DlLevelEntry entries[] = {
    {"plain","Plain",load_plain}, {"textured","Textured",load_textured}
};
static const DlBundle bundle = {"Tests",entries,2,0,-1,true,true};

int main(void) {
    DlLauncher launcher;
    assert(!dl_launcher_init(NULL,&bundle));
    assert(!dl_launcher_init(&launcher,NULL));
    assert(dl_launcher_update(&launcher,(DlLaunchInput){.accept=true})==DL_LAUNCH_NONE);
    DlBundle bad=bundle;
    bad.level_count=0; assert(!dl_launcher_init(&launcher,&bad));
    bad.level_count=DL_MAX_BUNDLE_LEVELS+1; assert(!dl_launcher_init(&launcher,&bad));
    DlLevelEntry missing={"missing","Missing",load_null};
    bad=bundle;bad.levels=&missing;bad.level_count=1;
    assert(!dl_launcher_init(&launcher,&bad));
    missing.load=load_malformed;
    malformed.test_start_count=INT_MAX;
    assert(!dl_launcher_init(&launcher,&bad));
    malformed.test_start_count=1;
    assert(!dl_launcher_init(&launcher,&bad));
    assert(dl_launcher_init(&launcher,&bundle));
    assert(launcher.menu_open&&!launcher.has_active);
    assert(dl_launcher_active_level(&launcher)==NULL);
    assert(dl_launcher_update(&launcher,(DlLaunchInput){.back=true})==DL_LAUNCH_NONE);
    assert(launcher.menu_open);
    dl_launcher_update(&launcher,(DlLaunchInput){.level_delta=-1});
    assert(launcher.selected_level==1&&launcher.selected_start==-1);
    dl_launcher_update(&launcher,(DlLaunchInput){.start_delta=-1});
    assert(launcher.selected_start==1);
    assert(strcmp(dl_launcher_start_label(&launcher),"Rooftop")==0);
    dl_launcher_update(&launcher,(DlLaunchInput){.start_delta=1});
    assert(launcher.selected_start==-1);
    assert(strcmp(dl_launcher_start_label(&launcher),"Default spawn")==0);
    dl_launcher_update(&launcher,(DlLaunchInput){.start_delta=1});
    assert(dl_launcher_update(&launcher,(DlLaunchInput){.accept=true})==DL_LAUNCH_LEVEL);
    assert(!launcher.menu_open&&launcher.has_active&&launcher.active_start==0);
    assert(dl_launcher_active_level(&launcher)==&textured);
    assert(dl_launcher_update(&launcher,(DlLaunchInput){.accept=true,.level_delta=1})==DL_LAUNCH_NONE);
    assert(launcher.active_level==1);
    assert(dl_launcher_update(&launcher,(DlLaunchInput){.open_menu=true})==DL_LAUNCH_MENU_OPENED);
    dl_launcher_update(&launcher,(DlLaunchInput){.level_delta=1});
    assert(launcher.selected_level==0&&launcher.selected_start==-1);
    assert(dl_launcher_update(&launcher,(DlLaunchInput){.back=true,.accept=true})==DL_LAUNCH_RESUME);
    assert(launcher.active_level==1&&launcher.active_start==0);
    dl_launcher_update(&launcher,(DlLaunchInput){.open_menu=true});
    assert(launcher.selected_level==1&&launcher.selected_start==0);
    dl_launcher_update(&launcher,(DlLaunchInput){.level_delta=1,.start_delta=1});
    assert(launcher.selected_start==-1); /* no presets on plain level */
    dl_launcher_update(&launcher,(DlLaunchInput){.level_delta=INT_MAX,.start_delta=INT_MIN});
    assert(launcher.selected_level==1&&launcher.selected_start>=-1&&launcher.selected_start<2);
    DlBundle direct=bundle;direct.start_in_menu=false;direct.initial_level=1;direct.initial_start=1;
    assert(dl_launcher_init(&launcher,&direct));
    assert(!launcher.menu_open&&launcher.has_active&&launcher.active_level==1&&launcher.active_start==1);
    direct.initial_level=999;direct.initial_start=999;
    assert(dl_launcher_init(&launcher,&direct));
    assert(launcher.active_level==0&&launcher.active_start==-1);
    puts("launch tests passed: invalid bundles, direct starts, navigation, presets, resume");
    return 0;
}
