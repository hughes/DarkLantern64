#include "game.h"
#include <assert.h>
#include <stdio.h>

int main(void) {
    DlCollider boxes[]={{{0,-.25f,0},{30,.25f,30},0,false},{{10,1,0},{.3f,1,1},0,true},{{20,1,0},{.3f,1,1},0,true}};
    int16_t mapping[]={-1,0,1};
    DlObjectDef objects[]={
        {.id="a",.kind=DL_OBJECT_DOOR,.position={10,1,0}},
        {.id="b",.kind=DL_OBJECT_DOOR,.position={20,1,0}},
        {.id="a-control",.kind=DL_OBJECT_CONTROL,.position={0,1,1},.targets=1,.radius=1.5f},
        {.id="both",.kind=DL_OBJECT_CONTROL,.position={20,1,1},.targets=3,.radius=1.5f},
        {.id="gold",.kind=DL_OBJECT_LOOT,.position={5,1,0},.value=10,.radius=1.5f},
        {.id="exit",.kind=DL_OBJECT_OBJECTIVE,.position={0,0,0},.required_loot=10,.radius=1.5f,.zone=true}
    };
    DlLevel level={.version=2,.colliders=boxes,.collider_count=3,.collider_objects=mapping,.objects=objects,.object_count=6};
    DlGame game;dl_game_init(&game,&level);
    dl_game_update(&game,&(DlInput){.use=true},1.f/60);
    assert(dl_game_object_open(&game,0)&&!dl_game_object_open(&game,1));
    assert(dl_position_clear(&game,(DlVec3){10,0,0},.18f,1.65f));
    assert(!dl_position_clear(&game,(DlVec3){20,0,0},.18f,1.65f));
    assert(!game.complete);
    game.player=(DlVec3){5,0,0};dl_game_update(&game,&(DlInput){.use=true},1.f/60);
    assert(game.loot_value==10&&!game.complete);
    dl_game_update(&game,&(DlInput){.use=true},1.f/60);assert(game.loot_value==10);
    assert(!dl_game_model_visible(&game,&(DlModelInstance){.id="gold",.role=DL_MODEL_LOOT}));
    game.player=(DlVec3){0,0,0};dl_game_update(&game,&(DlInput){0},1.f/60);assert(game.complete);
    dl_game_init(&game,&level);assert(!game.complete&&!game.loot_value&&!game.collected_objects);
    /* Both open, player inside B: closing the group is an atomic no-op. */
    game.open_objects=3;game.door_open=true;game.player=(DlVec3){20,0,0};
    dl_game_update(&game,&(DlInput){.use=true},1.f/60);
    assert(dl_game_object_open(&game,0)&&dl_game_object_open(&game,1));
    assert(game.object_revision==0);
    DlStartPreset start={.id="open",.door_open=true};level.test_starts=&start;level.test_start_count=1;
    assert(dl_game_start(&game,&level,0)&&dl_game_object_open(&game,0)&&dl_game_object_open(&game,1));
    puts("PASS independent doors, atomic group activation, loot, objective zone and restart");
    DlVec3 route[]={{-20,0,0},{20,0,0}};DlEnemyDef enemies[DL_MAX_ENEMIES];DlFootContacts contacts[DL_MAX_ENEMIES];
    for(int i=0;i<DL_MAX_ENEMIES;++i){enemies[i]=(DlEnemyDef){.id="guard",.spawn={-10,0,0},.patrol=route,.patrol_count=2,.speed=4};contacts[i]=(DlFootContacts){.stride=.01f,.phases={0,2000,4000,6000,8000,10000,12000,14000},.count=8};}
    level.enemies=enemies;level.enemy_count=DL_MAX_ENEMIES;level.foot_contacts=contacts;
    dl_game_init(&game,&level);dl_game_update(&game,&(DlInput){0},.25f);
    assert(game.event_count==DL_EVENT_CAPACITY&&game.event_overflow>0);
    puts("PASS bounded semantic event queue reports saturation without overrunning storage");
}
