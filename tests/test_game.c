#include "game.h"
#include "render_camera.h"
#include <math.h>
#include <stdio.h>
#include <string.h>

#define PI 3.14159265358979323846f
#define DT (1.0f / 60.0f)
#define CHECK(c) do { if (!(c)) { fprintf(stderr, "%s:%d: %s\n", __FILE__, __LINE__, #c); return false; } } while (0)
#define NEAR(a,b) (fabsf((a)-(b)) < .002f)
typedef struct { DlCollider boxes[24]; DlVec3 patrol[4]; DlLight lights[2]; DlEnemyDef enemies[DL_MAX_ENEMIES]; DlLevel level; } Fixture;
static void fixture_init(Fixture *f) {
    memset(f,0,sizeof(*f));
    f->boxes[0]=(DlCollider){{0,-.25f,0},{20,.25f,20},0,false};
    f->level=(DlLevel){.version=2,.title="Synthetic 3D tests",.colliders=f->boxes,.collider_count=1,
        .enemies=f->enemies,.enemy_count=1,.control={-15,1,-15},.objective={15,1,15},.lights=f->lights};
    f->enemies[0]=(DlEnemyDef){.id="test-watchman",.spawn={12,0,12},.patrol=f->patrol};
}
static void box(Fixture *f,DlVec3 center,DlVec3 half,float yaw,bool door) {
    f->boxes[f->level.collider_count++]=(DlCollider){center,half,yaw,door};
}
static void advance(DlGame *g,DlInput in,float seconds) {
    int count=(int)ceilf(seconds*60);
    for(int i=0;i<count;++i) dl_game_update(g,&in,seconds/count);
}
static bool clear_actors(const DlGame *g) {
    if (!dl_position_clear(g,g->player,.18f,g->crouched?.95f:1.65f)) return false;
    for (int i=0;i<g->enemy_count;++i)
        if (!dl_position_clear(g,g->enemies[i].position,.20f,1.65f)) return false;
    return true;
}
static bool rotated_wall_sliding(void) {
    Fixture f; fixture_init(&f);
    box(&f,(DlVec3){0,1.5f,0},(DlVec3){.15f,1.5f,8},PI/4,false);
    f.level.spawn=(DlVec3){-2,0,0}; f.level.spawn_yaw=PI/2;
    DlGame g; dl_game_init(&g,&f.level);
    for(int i=0;i<120;++i) {
        dl_game_update(&g,&(DlInput){.forward=1},DT);
        CHECK(clear_actors(&g));
        CHECK((g.player.x-g.player.z)/sqrtf(2)<-.329f);
    }
    CHECK(g.player.z>1 && g.player.x>.3f && g.player.x<1.2f && NEAR(g.player.y,0));
    CHECK(!dl_line_of_sight(&g,(DlVec3){-2,1,0},(DlVec3){2,1,0}));
    CHECK(dl_line_of_sight(&g,(DlVec3){-2,4,0},(DlVec3){2,4,0}));
    CHECK(!dl_position_clear(&g,(DlVec3){0,0,0},.18f,1.65f));
    return true;
}
static bool stairs_and_falling(void) {
    Fixture f; fixture_init(&f);
    for(int i=0;i<4;++i) {
        float top=.2f*(i+1);
        box(&f,(DlVec3){0,top/2,1.2f+.4f*i},(DlVec3){.7f,top/2,.2f},0,false);
    }
    box(&f,(DlVec3){0,.4f,3.1f},(DlVec3){1,.4f,.5f},0,false);
    DlGame g; dl_game_init(&g,&f.level); bool landed[4]={false};
    for(int i=0;i<180;++i) {
        dl_game_update(&g,&(DlInput){.forward=1,.crouch=true},DT);
        CHECK(clear_actors(&g) && g.grounded);
        for(int j=0;j<4;++j) if(NEAR(g.player.y,.2f*(j+1))) landed[j]=true;
    }
    CHECK(landed[0] && landed[1] && landed[2] && landed[3]);
    CHECK(NEAR(g.player.y,.8f) && g.player.z>2.9f);
    bool airborne=false;
    for(int i=0;i<120;++i) {
        dl_game_update(&g,&(DlInput){.strafe=1,.crouch=true},DT);
        airborne |= !g.grounded; CHECK(clear_actors(&g));
    }
    CHECK(airborne && g.grounded && NEAR(g.player.y,0)); return true;
}
static bool jump_gravity_and_ceiling(void) {
    Fixture f; fixture_init(&f); DlGame g; dl_game_init(&g,&f.level);
    dl_game_update(&g,&(DlInput){.jump=true},DT);
    CHECK(!g.grounded && g.vertical_velocity>0 && g.player.y>0);
    float peak=g.player.y;
    for(int i=0;i<70;++i) {
        /* Airborne jump inputs must not create extra jumps. */
        dl_game_update(&g,&(DlInput){.jump=i<20},DT); peak=fmaxf(peak,g.player.y);
        CHECK(clear_actors(&g));
    }
    CHECK(peak>.82f && peak<.90f && g.grounded && NEAR(g.player.y,0));
    box(&f,(DlVec3){0,2.1f,0},(DlVec3){2,.1f,2},0,false);
    dl_game_init(&g,&f.level); dl_game_update(&g,&(DlInput){.jump=true},DT); peak=g.player.y;
    for(int i=0;i<60;++i) {
        dl_game_update(&g,&(DlInput){0},DT); peak=fmaxf(peak,g.player.y);
        CHECK(clear_actors(&g) && g.player.y+1.65f<=2.001f);
    }
    CHECK(NEAR(peak,.35f) && g.grounded && NEAR(g.player.y,0)); return true;
}
static bool high_obstacle_requires_jump(void) {
    Fixture f; fixture_init(&f); f.level.spawn.z=.7f;
    box(&f,(DlVec3){0,.25f,1.6f},(DlVec3){2,.25f,.3f},0,false);
    DlGame walking,jumping; dl_game_init(&walking,&f.level);
    advance(&walking,(DlInput){.forward=1},1);
    CHECK(walking.player.z<1.121f && NEAR(walking.player.y,0));
    dl_game_init(&jumping,&f.level); dl_game_update(&jumping,&(DlInput){.forward=1,.jump=true},DT);
    float peak=0;
    for(int i=0;i<90;++i) {
        dl_game_update(&jumping,&(DlInput){.forward=1},DT); peak=fmaxf(peak,jumping.player.y);
        CHECK(clear_actors(&jumping));
    }
    CHECK(peak>.8f && jumping.player.z>3 && jumping.grounded); return true;
}
static bool crouch_headroom(void) {
    Fixture f; fixture_init(&f); f.level.spawn.z=-2;
    box(&f,(DlVec3){0,1.6f,0},(DlVec3){1,.5f,1},0,false);
    DlGame g; dl_game_init(&g,&f.level); advance(&g,(DlInput){.forward=1},1);
    CHECK(g.player.z<-1.179f && !g.crouched);
    advance(&g,(DlInput){.forward=1,.crouch=true},1.2f);
    CHECK(g.player.z>-.1f && g.player.z<.1f && g.crouched);
    dl_game_update(&g,&(DlInput){0},DT);
    CHECK(g.crouched && clear_actors(&g) && NEAR(dl_player_eye(&g).y,.8f));
    advance(&g,(DlInput){.forward=1},1.5f);
    CHECK(g.player.z>1.2f && !g.crouched && clear_actors(&g) && NEAR(dl_player_eye(&g).y,1.5f));
    return true;
}
static bool stacked_floors_and_vertical_perception(void) {
    Fixture f; fixture_init(&f);
    box(&f,(DlVec3){0,3.1f,0},(DlVec3){2,.1f,2},0,false);
    f.enemies[0].spawn=(DlVec3){0,3.2f,1}; f.enemies[0].sight_range=8;
    f.lights[0]=(DlLight){.position={0,4.8f,0},.radius=8,.intensity=1}; f.level.light_count=1;
    f.level.objective=(DlVec3){0,4,0};
    DlGame below,above; dl_game_init(&below,&f.level);
    CHECK(clear_actors(&below));
    CHECK(!dl_line_of_sight(&below,(DlVec3){0,1.5f,0},(DlVec3){0,4.7f,0}));
    CHECK(dl_visibility(&below,dl_player_eye(&below))<=.081f);
    advance(&below,(DlInput){.use=true},1);
    CHECK(!below.complete && !below.enemies[0].sees_player && NEAR(below.enemies[0].position.y,3.2f) && NEAR(below.player.y,0));
    f.level.spawn=(DlVec3){0,3.2f,0}; f.enemies[0].sight_range=0; dl_game_init(&above,&f.level);
    CHECK(above.grounded && clear_actors(&above) && dl_visibility(&above,dl_player_eye(&above))>.9f);
    dl_game_update(&above,&(DlInput){.use=true},DT); CHECK(above.complete); return true;
}
static bool linked_door_and_occupancy(void) {
    Fixture f; fixture_init(&f);
    box(&f,(DlVec3){0,1.5f,1},(DlVec3){1,1.5f,.1f},0,true);
    /* All authored proxies of the single linked door toggle together. */
    box(&f,(DlVec3){4,1.5f,1},(DlVec3){1,1.5f,.1f},0,true);
    f.level.control=(DlVec3){0,1,.3f}; DlGame g; dl_game_init(&g,&f.level);
    CHECK(!dl_line_of_sight(&g,(DlVec3){0,1,0},(DlVec3){0,1,2}));
    CHECK(!dl_position_clear(&g,(DlVec3){4,0,1},.18f,1.65f));
    dl_game_update(&g,&(DlInput){.use=true},DT);
    CHECK(g.door_open && g.event==DL_EVENT_DOOR && g.sound_serial==1);
    CHECK(dl_line_of_sight(&g,(DlVec3){0,1,0},(DlVec3){0,1,2}));
    CHECK(dl_position_clear(&g,(DlVec3){4,0,1},.18f,1.65f));
    advance(&g,(DlInput){.forward=1,.crouch=true},1);
    dl_game_update(&g,&(DlInput){.use=true,.crouch=true},DT); CHECK(g.door_open);
    advance(&g,(DlInput){.forward=-1,.crouch=true},1);
    dl_game_update(&g,&(DlInput){.use=true},DT); CHECK(!g.door_open);
    f.level.control.y=5;
    dl_game_update(&g,&(DlInput){.use=true},DT); CHECK(!g.door_open);
    return true;
}
static bool light_crouch_and_occlusion(void) {
    Fixture f; fixture_init(&f); f.level.spawn=(DlVec3){0,0,3}; f.enemies[0].spawn=(DlVec3){0,0,0};
    f.enemies[0].sight_range=8; f.lights[0]=(DlLight){.position={0,1.5f,3},.radius=6,.intensity=1}; f.level.light_count=1;
    DlGame standing,crouched,shadow; dl_game_init(&standing,&f.level); dl_game_init(&crouched,&f.level);
    advance(&standing,(DlInput){0},.5f); advance(&crouched,(DlInput){.crouch=true},.5f);
    CHECK(standing.enemies[0].sees_player && crouched.enemies[0].sees_player);
    CHECK(standing.enemies[0].awareness>crouched.enemies[0].awareness+.2f && crouched.visibility<standing.visibility);
    advance(&standing,(DlInput){0},1);
    CHECK(standing.enemies[0].state==DL_CHASE && standing.event==DL_EVENT_DETECTED);
    f.level.light_count=0; dl_game_init(&shadow,&f.level); advance(&shadow,(DlInput){.crouch=true},2);
    CHECK(shadow.enemies[0].awareness==0 && !shadow.enemies[0].sees_player);
    f.level.light_count=1; box(&f,(DlVec3){0,1.5f,1.5f},(DlVec3){2,1.5f,.1f},0,false);
    dl_game_init(&shadow,&f.level); advance(&shadow,(DlInput){0},1);
    CHECK(!shadow.enemies[0].sees_player && shadow.enemies[0].awareness==0);
    CHECK(dl_visibility(&shadow,(DlVec3){0,1.5f,0})<=.081f); return true;
}
static bool sound_occlusion_and_recovery(void) {
    Fixture f; fixture_init(&f); f.level.spawn.z=4; f.enemies[0].spawn=(DlVec3){0,0,0}; f.enemies[0].hearing_range=8;
    DlGame g; dl_game_init(&g,&f.level); dl_game_update(&g,&(DlInput){.noise=true},DT);
    CHECK(g.enemies[0].heard_sound && g.enemies[0].state==DL_INVESTIGATE && g.event==DL_EVENT_NOISE && g.sound_serial==1);
    CHECK(NEAR(g.enemies[0].investigate_target.z,4) && NEAR(g.enemies[0].investigate_target.y,0));
    advance(&g,(DlInput){0},1); CHECK(!g.enemies[0].heard_sound);
    advance(&g,(DlInput){0},8); CHECK(g.enemies[0].state==DL_PATROL);
    box(&f,(DlVec3){0,1.5f,2},(DlVec3){2,1.5f,.1f},0,false);
    dl_game_init(&g,&f.level); dl_game_update(&g,&(DlInput){.noise=true},DT);
    CHECK(!g.enemies[0].heard_sound && g.enemies[0].state==DL_PATROL);
    f.level.spawn.z=3; f.enemies[0].spawn.z=1;
    dl_game_init(&g,&f.level); dl_game_update(&g,&(DlInput){.noise=true},DT);
    CHECK(g.enemies[0].heard_sound); /* Nearby sounds survive the 30% occluded range. */
    return true;
}
static bool footstep_speed_and_sound(void) {
    Fixture f; fixture_init(&f); f.enemies[0].spawn=(DlVec3){2.5f,0,.5f}; f.enemies[0].hearing_range=7;
    DlGame standing,crouched; dl_game_init(&standing,&f.level); dl_game_init(&crouched,&f.level);
    advance(&standing,(DlInput){.forward=1},.6f); advance(&crouched,(DlInput){.forward=1,.crouch=true},.6f);
    CHECK(standing.player.z>crouched.player.z+.5f && standing.sound_serial>0 && crouched.sound_serial>0);
    CHECK(standing.enemies[0].state==DL_INVESTIGATE && crouched.enemies[0].state==DL_PATROL);
    CHECK(standing.event==DL_EVENT_STEP && crouched.event==DL_EVENT_STEP); return true;
}
static bool patrol_world_waypoints(void) {
    Fixture f; fixture_init(&f); f.enemies[0].spawn=(DlVec3){-4,0,-2}; f.enemies[0].speed=1.5f; f.enemies[0].patrol_count=4;
    f.patrol[0]=f.enemies[0].spawn; f.patrol[1]=(DlVec3){-4,.2f,2};
    f.patrol[2]=(DlVec3){-1,.2f,2}; f.patrol[3]=(DlVec3){-1,0,-2};
    box(&f,(DlVec3){-2.5f,.1f,2},(DlVec3){2.5f,.1f,.7f},0,false);
    DlGame g; dl_game_init(&g,&f.level); bool visited[4]={false};
    for(int i=0;i<1200;++i) {
        dl_game_update(&g,&(DlInput){0},DT); CHECK(clear_actors(&g) && isfinite(g.enemies[0].position.y));
        CHECK(g.enemies[0].patrol_index>=0 && g.enemies[0].patrol_index<4 && g.enemies[0].state==DL_PATROL);
        for(int j=0;j<4;++j) {
            DlVec3 p=f.patrol[j];
            if(fabsf(g.enemies[0].position.x-p.x)<.15f && fabsf(g.enemies[0].position.y-p.y)<.01f && fabsf(g.enemies[0].position.z-p.z)<.15f) visited[j]=true;
        }
    }
    CHECK(visited[0] && visited[1] && visited[2] && visited[3]);
    box(&f,(DlVec3){-4,1.5f,-.5f},(DlVec3){1,1.5f,.1f},0,false);
    dl_game_init(&g,&f.level); advance(&g,(DlInput){0},5);
    CHECK(g.enemies[0].position.z<-.799f && clear_actors(&g));
    /* Explicit waypoint segments must be clear; automatic detours are future work. */
    return true;
}
static bool viewcone_capture_and_decay(void) {
    Fixture f; fixture_init(&f); f.level.spawn.z=-3; f.enemies[0].spawn=(DlVec3){0,0,0}; f.enemies[0].sight_range=8;
    f.lights[0]=(DlLight){.position={0,1.5f,-3},.radius=8,.intensity=1}; f.level.light_count=1;
    DlGame g; dl_game_init(&g,&f.level); advance(&g,(DlInput){0},1);
    CHECK(g.enemies[0].awareness==0 && !g.enemies[0].sees_player);
    g.enemies[0].yaw=PI; /* Controlled facing experiment; player movement is not bypassed. */
    advance(&g,(DlInput){0},.5f); CHECK(g.enemies[0].awareness>.5f && g.enemies[0].sees_player);
    float previous=g.enemies[0].awareness; f.level.light_count=0; advance(&g,(DlInput){0},.5f);
    CHECK(g.enemies[0].awareness<previous && !g.enemies[0].sees_player);
    f.level.light_count=1; f.enemies[0].speed=1.5f; advance(&g,(DlInput){0},5);
    CHECK(g.caught && g.enemies[0].state==DL_CHASE);
    DlVec3 frozen=g.player; advance(&g,(DlInput){.forward=1},1);
    CHECK(NEAR(g.player.x,frozen.x) && NEAR(g.player.y,frozen.y) && NEAR(g.player.z,frozen.z));
    dl_game_update(&g,&(DlInput){.restart=true},DT); CHECK(!g.caught && g.enemies[0].state==DL_PATROL); return true;
}
static bool objective_reset_and_time_inputs(void) {
    Fixture f; fixture_init(&f); f.level.objective=(DlVec3){0,1,1}; DlGame g; dl_game_init(&g,&f.level);
    dl_game_update(&g,&(DlInput){.use=true},DT); CHECK(g.complete && g.event==DL_EVENT_OBJECTIVE);
    advance(&g,(DlInput){.forward=1},1); CHECK(NEAR(g.player.z,0));
    g.door_open=true; g.enemies[0].awareness=1; g.caught=true;
    dl_game_update(&g,&(DlInput){.restart=true},0);
    CHECK(!g.complete && !g.caught && !g.door_open && g.enemies[0].awareness==0 && g.elapsed==0 && g.sound_serial==0 && g.grounded);
    dl_game_update(&g,&(DlInput){.forward=1},NAN); dl_game_update(&g,&(DlInput){.forward=1},-1);
    dl_game_update(&g,&(DlInput){.forward=1},INFINITY); CHECK(g.elapsed==0 && NEAR(g.player.z,0));
    dl_game_update(&g,&(DlInput){.forward=NAN,.strafe=INFINITY,.turn=NAN,.look=INFINITY},DT);
    CHECK(NEAR(g.player.z,0) && isfinite(g.yaw) && isfinite(g.pitch));
    dl_game_init(&g,&f.level); dl_game_update(&g,&(DlInput){.forward=1,.strafe=1,.look=1},1000);
    CHECK(NEAR(g.elapsed,.25f) && NEAR(sqrtf(g.player.x*g.player.x+g.player.z*g.player.z),.525f));
    advance(&g,(DlInput){.look=1},3); CHECK(NEAR(g.pitch,1.35f));
    advance(&g,(DlInput){.look=-1},3); CHECK(NEAR(g.pitch,-1.35f)); return true;
}
static bool moonlight_surface_and_shelter(void) {
    Fixture f; fixture_init(&f);
    f.level.environment=(DlEnvironment){.enabled=true,.ambient={.01f,.02f,.03f},
        .moon_direction={0,4,0},.moon_color={.25f,.5f,1},.moon_intensity=.6f,.exposure=1};
    DlGame g; dl_game_init(&g,&f.level);
    DlVec3 sample={0,1,0};
    DlVec3 up=dl_surface_light(&g,sample,(DlVec3){0,2,0});
    DlVec3 down=dl_surface_light(&g,sample,(DlVec3){0,-1,0});
    CHECK(NEAR(up.x,.16f) && NEAR(up.y,.32f) && NEAR(up.z,.63f));
    CHECK(NEAR(down.x,.01f) && NEAR(down.y,.02f) && NEAR(down.z,.03f));
    float outdoor=dl_visibility(&g,sample);
    CHECK(outdoor>.3f && outdoor<.32f);
    /* A real roof blocks the same moon for rendering and stealth perception. */
    box(&f,(DlVec3){0,2.5f,0},(DlVec3){2,.1f,2},0,false);
    DlVec3 covered=dl_surface_light(&g,sample,(DlVec3){0,1,0});
    CHECK(NEAR(covered.x,down.x) && NEAR(covered.z,down.z));
    CHECK(dl_visibility(&g,sample)<.021f && dl_visibility(&g,(DlVec3){3,1,0})>outdoor-.001f);
    /* Presentation brightness cannot secretly change the AI light meter. */
    f.level.environment.exposure=8;
    CHECK(NEAR(dl_visibility(&g,(DlVec3){3,1,0}),outdoor));
    return true;
}
static bool colored_point_light_attenuation(void) {
    Fixture f; fixture_init(&f); f.level.environment.enabled=true;
    f.level.light_count=1;
    f.lights[0]=(DlLight){.position={0,3,0},.radius=4,.intensity=2,.color={1,.25f,.05f}};
    DlGame g; dl_game_init(&g,&f.level);
    DlVec3 near=dl_surface_light(&g,(DlVec3){0,2,0},(DlVec3){0,1,0});
    DlVec3 far=dl_surface_light(&g,(DlVec3){0,1,0},(DlVec3){0,1,0});
    DlVec3 back=dl_surface_light(&g,(DlVec3){0,2,0},(DlVec3){0,-1,0});
    CHECK(NEAR(near.x,1.125f) && NEAR(near.y,.28125f) && NEAR(near.z,.05625f));
    CHECK(NEAR(far.x,.5f) && back.x==0 && back.y==0 && back.z==0);
    DlVec3 beyond=dl_surface_light(&g,(DlVec3){0,7,0},(DlVec3){0,-1,0});
    CHECK(beyond.x==0);
    float expected=.2126f*near.x+.7152f*near.y+.0722f*near.z;
    CHECK(NEAR(dl_visibility(&g,(DlVec3){0,2,0}),expected));
    box(&f,(DlVec3){0,2.5f,0},(DlVec3){2,.1f,2},0,true);
    CHECK(dl_visibility(&g,(DlVec3){0,2,0})==0);
    g.door_open=true;
    CHECK(NEAR(dl_visibility(&g,(DlVec3){0,2,0}),expected));
    return true;
}
static bool lighting_legacy_and_invalid_inputs(void) {
    Fixture f; fixture_init(&f); f.level.light_count=1;
    f.lights[0]=(DlLight){.position={0,3,0},.radius=4,.intensity=1,.color={1,0,0}};
    DlGame g; dl_game_init(&g,&f.level);
    CHECK(NEAR(dl_visibility(&g,(DlVec3){0,1,0}),.33f)); /* legacy ignores RGB */
    f.level.environment.enabled=true;
    f.lights[0].color=(DlVec3){0};
    DlVec3 white=dl_surface_light(&g,(DlVec3){0,1,0},(DlVec3){0,1,0});
    CHECK(NEAR(white.x,.25f) && NEAR(white.y,.25f) && NEAR(white.z,.25f));
    DlVec3 coincident=dl_surface_light(&g,f.lights[0].position,(DlVec3){0,1,0});
    CHECK(NEAR(coincident.x,1));
    f.lights[0].intensity=INFINITY;
    f.level.environment.moon_direction=(DlVec3){NAN,1,0};
    f.level.environment.moon_intensity=1;
    f.level.environment.ambient=(DlVec3){NAN,0,0};
    DlVec3 invalid=dl_surface_light(&g,(DlVec3){0,1,0},(DlVec3){0,1,0});
    CHECK(invalid.x==0 && invalid.y==0 && invalid.z==0 && dl_visibility(&g,(DlVec3){0,1,0})==0);
    CHECK(dl_surface_light(NULL,(DlVec3){0},(DlVec3){0,1,0}).x==0);
    CHECK(dl_surface_light(&g,(DlVec3){NAN,0,0},(DlVec3){0,1,0}).x==0);
    return true;
}
static bool independent_enemy_patrols_and_restart(void) {
    Fixture f; fixture_init(&f); f.level.enemy_count=2;
    DlVec3 west_route[]={{-4,0,-4},{-2,0,-4}};
    DlVec3 east_route[]={{4,0,4},{4,0,6}};
    f.enemies[0]=(DlEnemyDef){.id="west-watchman",.spawn={-4,0,-4},.speed=1,
        .patrol=west_route,.patrol_count=2};
    f.enemies[1]=(DlEnemyDef){.id="east-scout",.type=DL_ENEMY_SCOUT,.spawn={4,0,4},.speed=2,
        .patrol=east_route,.patrol_count=2};
    DlGame g; dl_game_init(&g,&f.level); advance(&g,(DlInput){0},.5f);
    CHECK(g.enemy_count==2 && clear_actors(&g));
    CHECK(NEAR(g.enemies[0].position.x,-3.5f) && NEAR(g.enemies[0].position.z,-4));
    CHECK(NEAR(g.enemies[1].position.x,4) && NEAR(g.enemies[1].position.z,5));
    CHECK(NEAR(g.enemies[0].yaw,PI/2) && NEAR(g.enemies[1].yaw,0));
    /* Returning from search chooses each actor's own route, not the first route. */
    g.enemies[1].state=DL_SEARCH; g.enemies[1].search_timer=.001f;
    dl_game_update(&g,&(DlInput){0},DT);
    CHECK(g.enemies[1].state==DL_PATROL && g.enemies[1].patrol_index==1);
    g.enemies[0].awareness=.6f; g.enemies[1].heard_sound=true; g.door_open=true;
    dl_game_update(&g,&(DlInput){.restart=true},DT);
    CHECK(g.enemy_count==2 && !g.door_open && g.elapsed==0);
    CHECK(NEAR(g.enemies[0].position.x,-4) && NEAR(g.enemies[1].position.z,4));
    CHECK(g.enemies[0].awareness==0 && !g.enemies[1].heard_sound);
    CHECK(g.enemies[0].state==DL_PATROL && g.enemies[1].state==DL_PATROL);
    return true;
}
static bool sound_reaches_each_enemy_independently(void) {
    Fixture f; fixture_init(&f); f.level.enemy_count=2; f.level.spawn.z=4;
    f.enemies[0].hearing_range=1;
    f.enemies[1]=(DlEnemyDef){.id="near-scout",.type=DL_ENEMY_SCOUT,.hearing_range=8};
    DlGame g; dl_game_init(&g,&f.level);
    dl_game_update(&g,&(DlInput){.noise=true},DT);
    CHECK(!g.enemies[0].heard_sound && g.enemies[0].state==DL_PATROL);
    CHECK(g.enemies[1].heard_sound && g.enemies[1].state==DL_INVESTIGATE);
    CHECK(NEAR(g.enemies[1].investigate_target.z,4) && NEAR(g.enemies[1].investigate_target.y,0));
    f.enemies[0].hearing_range=20;
    dl_game_update(&g,&(DlInput){.noise=true},DT);
    CHECK(g.enemies[0].heard_sound && g.enemies[1].heard_sound && g.sound_serial==2);
    CHECK(g.enemies[0].state==DL_INVESTIGATE && g.enemies[1].state==DL_INVESTIGATE);
    advance(&g,(DlInput){0},1);
    CHECK(!g.enemies[0].heard_sound && !g.enemies[1].heard_sound);
    return true;
}
static bool second_enemy_detects_and_catches(void) {
    Fixture f; fixture_init(&f); f.level.enemy_count=2; f.level.spawn.z=3;
    f.enemies[1]=(DlEnemyDef){.id="second-watchman",.speed=1.5f,.sight_range=8};
    f.lights[0]=(DlLight){.position={0,1.5f,3},.radius=8,.intensity=1}; f.level.light_count=1;
    DlGame g; dl_game_init(&g,&f.level); advance(&g,(DlInput){0},.5f);
    CHECK(g.enemies[1].sees_player && g.enemies[1].awareness>.5f);
    CHECK(!g.enemies[0].sees_player && g.enemies[0].awareness==0);
    advance(&g,(DlInput){0},5);
    CHECK(g.caught && g.enemies[1].state==DL_CHASE && g.enemies[0].state==DL_PATROL);
    return true;
}
static bool second_enemy_blocks_closing_door(void) {
    Fixture f; fixture_init(&f); f.level.enemy_count=2;
    box(&f,(DlVec3){0,1.5f,1},(DlVec3){1,1.5f,.1f},0,true);
    f.level.control=(DlVec3){0,1,.3f};
    f.enemies[1]=(DlEnemyDef){.id="door-watchman",.spawn={0,0,1}};
    DlGame g; dl_game_init(&g,&f.level); g.door_open=true;
    dl_game_update(&g,&(DlInput){.use=true},DT);
    CHECK(g.door_open && g.sound_serial==0);
    g.enemies[1].position.z=2;
    dl_game_update(&g,&(DlInput){.use=true},DT);
    CHECK(!g.door_open && g.event==DL_EVENT_DOOR);
    return true;
}
static bool sentry_returns_home_and_resumes_facing(void) {
    Fixture f; fixture_init(&f); f.level.spawn.z=2;
    f.enemies[0]=(DlEnemyDef){.id="door-sentry",.behavior=DL_BEHAVIOR_SENTRY,
        .yaw=PI/2,.speed=1,.hearing_range=8};
    DlGame g; dl_game_init(&g,&f.level); advance(&g,(DlInput){0},1);
    CHECK(NEAR(g.enemies[0].position.z,0) && NEAR(g.enemies[0].yaw,PI/2));
    dl_game_update(&g,&(DlInput){.noise=true},DT);
    advance(&g,(DlInput){0},1);
    CHECK(g.enemies[0].position.z>1 && g.enemies[0].state==DL_INVESTIGATE);
    advance(&g,(DlInput){0},7);
    CHECK(g.enemies[0].state==DL_PATROL && fabsf(g.enemies[0].position.z)<.011f);
    CHECK(NEAR(g.enemies[0].yaw,PI/2) && clear_actors(&g));
    return true;
}
static bool enemy_capacity_and_empty_levels(void) {
    Fixture f; fixture_init(&f); f.level.enemy_count=0;
    DlGame g; dl_game_init(&g,&f.level); advance(&g,(DlInput){.forward=1,.noise=true},.5f);
    CHECK(g.enemy_count==0 && g.player.z>1 && !g.caught);
    f.level.enemy_count=-1; dl_game_init(&g,&f.level); CHECK(g.enemy_count==0);
    f.level.enemy_count=DL_MAX_ENEMIES+1;
    for (int i=0;i<DL_MAX_ENEMIES;++i) f.enemies[i].spawn=(DlVec3){12,0,12};
    dl_game_init(&g,&f.level); CHECK(g.enemy_count==DL_MAX_ENEMIES);
    advance(&g,(DlInput){0},.1f); CHECK(clear_actors(&g));
    f.level.enemies=NULL; dl_game_init(&g,&f.level); CHECK(g.enemy_count==0);
    dl_game_update(&g,&(DlInput){.noise=true},DT); CHECK(!g.caught);
    return true;
}
static bool fallen_enemy_resets_independently(void) {
    Fixture f; fixture_init(&f); f.level.enemy_count=2;
    f.enemies[1]=(DlEnemyDef){.id="other-watchman",.spawn={-10,0,10}};
    DlGame g; dl_game_init(&g,&f.level); g.enemies[0].position.y=-21;
    g.enemies[1].awareness=.5f; g.door_open=true;
    dl_game_update(&g,&(DlInput){0},DT);
    CHECK(NEAR(g.enemies[0].position.y,0) && NEAR(g.enemies[0].position.x,12));
    CHECK(g.enemies[1].awareness>.49f && g.door_open && g.elapsed>0);
    return true;
}
static bool authored_start_resets_world_and_applies_stance_and_lighting(void) {
    Fixture f; fixture_init(&f); f.level.enemy_count=2;
    f.enemies[1]=(DlEnemyDef){.id="other-sentry",.behavior=DL_BEHAVIOR_SENTRY,
        .spawn={-10,0,10},.yaw=PI/2};
    DlStartPreset starts[]={
        {.id="crouched-entry",.label="Crouched entry",.position={2,0,0},.yaw=-PI/2,.pitch=-.4f,.door_open=true,.crouched=true},
        {.id="standing-entry",.label="Standing entry",.position={2,0,0},.yaw=PI,.pitch=.3f},
    };
    f.level.test_starts=starts; f.level.test_start_count=2;
    f.lights[0]=(DlLight){.position={2,.8f,0},.radius=4,.intensity=.8f}; f.level.light_count=1;
    DlGame g; dl_game_init(&g,&f.level);
    g.complete=true; g.caught=true; g.vertical_velocity=3; g.footstep_timer=.2f;
    g.sound_serial=10; g.event=DL_EVENT_DETECTED; g.elapsed=100; g.last_sound=(DlVec3){1,2,3};
    g.enemies[0].state=DL_CHASE; g.enemies[0].awareness=1; g.enemies[0].position=(DlVec3){0};
    g.enemies[1].heard_sound=true; g.enemies[1].sees_player=true;
    CHECK(dl_game_start(&g,&f.level,0));
    CHECK(NEAR(g.player.x,2) && NEAR(g.player.y,0) && NEAR(g.yaw,3*PI/2) && NEAR(g.pitch,-.4f));
    CHECK(g.crouched && g.door_open && g.grounded && !g.complete && !g.caught);
    CHECK(g.vertical_velocity==0 && g.footstep_timer==0 && g.sound_serial==0 && g.event==DL_EVENT_NONE);
    CHECK(g.elapsed==0 && g.sound_age==10 && g.last_sound.x==0 && g.last_sound.y==0);
    CHECK(g.enemy_count==2 && NEAR(g.enemies[0].position.x,12) && NEAR(g.enemies[1].position.x,-10));
    CHECK(g.enemies[0].state==DL_PATROL && g.enemies[0].awareness==0 && !g.enemies[1].heard_sound && !g.enemies[1].sees_player);
    CHECK(NEAR(dl_player_eye(&g).y,.8f) && NEAR(g.visibility,dl_visibility(&g,dl_player_eye(&g))*.55f));
    CHECK(NEAR(g.visibility,.88f*.55f));
    DlGame fresh=g;
    dl_game_update(&g,&(DlInput){.crouch=true,.forward=1,.noise=true},DT);
    CHECK(dl_game_start(&g,&f.level,0) && memcmp(&g,&fresh,sizeof(g))==0);
    CHECK(dl_game_start(&g,&f.level,1));
    CHECK(!g.crouched && !g.door_open && NEAR(g.yaw,PI) && NEAR(g.pitch,.3f));
    CHECK(NEAR(g.visibility,dl_visibility(&g,dl_player_eye(&g))) && NEAR(dl_player_eye(&g).y,1.5f));
    CHECK(dl_game_start(&g,&f.level,-1));
    DlGame ordinary; dl_game_init(&ordinary,&f.level);
    CHECK(memcmp(&g,&ordinary,sizeof(g))==0);
    return true;
}
static bool authored_start_support_uses_selected_door_state(void) {
    Fixture f; fixture_init(&f);
    box(&f,(DlVec3){2,1,0},(DlVec3){1,1,1},0,true);
    DlStartPreset starts[]={
        {.id="closed-platform",.position={2,2,0}},
        {.id="removed-platform",.position={2,2,0},.door_open=true},
    };
    f.level.test_starts=starts; f.level.test_start_count=2;
    DlGame g;
    CHECK(dl_game_start(&g,&f.level,0) && g.grounded);
    /* Malformed native data still computes support after opening the door;
     * the content compiler rejects this unsupported authored placement. */
    CHECK(dl_game_start(&g,&f.level,1) && !g.grounded);
    return true;
}
static bool invalid_start_selection_preserves_game(void) {
    Fixture f; fixture_init(&f);
    DlStartPreset start={.id="entry",.position={2,0,0}};
    f.level.test_starts=&start; f.level.test_start_count=1;
    DlGame g; dl_game_init(&g,&f.level);
    advance(&g,(DlInput){.forward=1,.noise=true},.1f);
    DlGame before=g;
    CHECK(!dl_game_start(&g,&f.level,-2) && memcmp(&g,&before,sizeof(g))==0);
    CHECK(!dl_game_start(&g,&f.level,1) && memcmp(&g,&before,sizeof(g))==0);
    CHECK(!dl_game_start(&g,NULL,-1) && memcmp(&g,&before,sizeof(g))==0);
    CHECK(!dl_game_start(NULL,&f.level,0));
    f.level.test_start_count=0;
    CHECK(!dl_game_start(&g,&f.level,0) && memcmp(&g,&before,sizeof(g))==0);
    CHECK(dl_game_start(&g,&f.level,-1) && NEAR(g.player.z,0));
    before=g; f.level.test_start_count=1; f.level.test_starts=NULL;
    CHECK(!dl_game_start(&g,&f.level,0) && memcmp(&g,&before,sizeof(g))==0);
    return true;
}
static float camera_dot(DlVec3 a,DlVec3 b) { return a.x*b.x+a.y*b.y+a.z*b.z; }
static bool camera_matches_right_handed_editor(void) {
    /* Same eye and heading as the authored loot-crate view. These asymmetric
     * positions must retain their order in LightEngine and the N64 game. */
    DlCameraBasis view=dl_camera_basis(PI,0);
    const DlVec3 coins={-.25f,-.198f,-1.65f},goblet={.15f,-.198f,-1.55f};
    float coins_x=160+164*camera_dot(coins,view.right)/camera_dot(coins,view.forward);
    float goblet_x=160+164*camera_dot(goblet,view.right)/camera_dot(goblet,view.forward);
    CHECK(NEAR(coins_x,135.15152f) && NEAR(goblet_x,175.87097f));
    CHECK(coins_x<160 && goblet_x>160);
    for(int y=0;y<13;++y) for(int p=-4;p<=4;++p) {
        float yaw=(y-6)*.57f,pitch=p*.3f;
        view=dl_camera_basis(yaw,pitch);
        /* A standard right-handed lookAt's screen-right axis is forward x
         * world-up. Normalize it independently of the runtime yaw formula. */
        float horizontal=sqrtf(view.forward.x*view.forward.x+view.forward.z*view.forward.z);
        DlVec3 expected_right={-view.forward.z/horizontal,0,view.forward.x/horizontal};
        CHECK(NEAR(camera_dot(view.right,expected_right),1));
        CHECK(NEAR(camera_dot(view.right,view.right),1) && NEAR(camera_dot(view.up,view.up),1));
        CHECK(NEAR(camera_dot(view.forward,view.forward),1));
        CHECK(NEAR(camera_dot(view.right,view.up),0) && NEAR(camera_dot(view.right,view.forward),0));
        CHECK(NEAR(camera_dot(view.up,view.forward),0));
        DlVec3 cross={view.right.y*view.up.z-view.right.z*view.up.y,
            view.right.z*view.up.x-view.right.x*view.up.z,view.right.x*view.up.y-view.right.y*view.up.x};
        CHECK(NEAR(camera_dot(cross,view.forward),-1));
        CHECK(NEAR(view.forward.x,sinf(yaw)*cosf(pitch)) && NEAR(view.forward.z,cosf(yaw)*cosf(pitch)));
    }
    return true;
}
static bool player_controls_follow_screen_right(void) {
    Fixture f; fixture_init(&f); f.level.enemy_count=0;
    f.level.spawn=(DlVec3){-2,0,3};
    const float headings[]={0,PI/2,PI,3*PI/2,.73f};
    for(unsigned i=0;i<sizeof(headings)/sizeof(headings[0]);++i) {
        f.level.spawn_yaw=headings[i];
        DlGame g; dl_game_init(&g,&f.level);
        DlCameraBasis view=dl_camera_basis(g.yaw,0);
        DlVec3 start=g.player;
        advance(&g,(DlInput){.strafe=1},.1f);
        DlVec3 movement={g.player.x-start.x,g.player.y-start.y,g.player.z-start.z};
        CHECK(NEAR(camera_dot(movement,view.right),.21f) && NEAR(camera_dot(movement,view.forward),0));
        CHECK(NEAR(g.yaw,headings[i]) && clear_actors(&g));
        dl_game_init(&g,&f.level);
        advance(&g,(DlInput){.forward=1},.1f);
        movement=(DlVec3){g.player.x-start.x,g.player.y-start.y,g.player.z-start.z};
        CHECK(NEAR(camera_dot(movement,view.forward),.21f) && NEAR(camera_dot(movement,view.right),0));
        dl_game_init(&g,&f.level);
        advance(&g,(DlInput){.turn=1},.1f);
        DlCameraBasis turned=dl_camera_basis(g.yaw,0);
        CHECK(camera_dot(turned.forward,view.right)>.22f);
        CHECK(NEAR(g.player.x,start.x) && NEAR(g.player.z,start.z));
        advance(&g,(DlInput){.turn=-1},.1f);
        turned=dl_camera_basis(g.yaw,0);
        CHECK(NEAR(camera_dot(turned.forward,view.forward),1));
        /* Runtime viewing/input must never rewrite authored placement/yaw. */
        CHECK(NEAR(f.level.spawn.x,-2) && NEAR(f.level.spawn.z,3) && NEAR(f.level.spawn_yaw,headings[i]));
    }
    return true;
}
int main(void) {
    struct { const char *name; bool (*run)(void); } cases[]={
        {"rotated wall collision and sliding",rotated_wall_sliding},{"stairs and falling",stairs_and_falling},
        {"jump, gravity, and ceiling",jump_gravity_and_ceiling},{"high obstacle requires jump",high_obstacle_requires_jump},
        {"crouch headroom",crouch_headroom},{"stacked floors and vertical perception",stacked_floors_and_vertical_perception},
        {"linked door and occupancy",linked_door_and_occupancy},{"light, crouch, and occlusion",light_crouch_and_occlusion},
        {"sound occlusion and recovery",sound_occlusion_and_recovery},{"footstep speed and sound",footstep_speed_and_sound},
        {"patrol world waypoints",patrol_world_waypoints},{"viewcone, capture, and decay",viewcone_capture_and_decay},
        {"objective, reset, and time inputs",objective_reset_and_time_inputs},
        {"moonlight, surface orientation, and covered/outdoor perception",moonlight_surface_and_shelter},
        {"colored point lights, falloff, and dynamic occlusion",colored_point_light_attenuation},
        {"legacy lighting and invalid lighting inputs",lighting_legacy_and_invalid_inputs},
        {"independent enemy routes and restart",independent_enemy_patrols_and_restart},
        {"sound reaches every eligible enemy",sound_reaches_each_enemy_independently},
        {"second enemy perception and capture",second_enemy_detects_and_catches},
        {"second enemy prevents a closing door",second_enemy_blocks_closing_door},
        {"sentry investigates, returns home, and restores facing",sentry_returns_home_and_resumes_facing},
        {"bounded enemy capacity and empty levels",enemy_capacity_and_empty_levels},
        {"fallen enemy resets without resetting the world",fallen_enemy_resets_independently},
        {"authored start resets the world, stance, and light meter",authored_start_resets_world_and_applies_stance_and_lighting},
        {"authored start support follows selected door state",authored_start_support_uses_selected_door_state},
        {"invalid start selection preserves the current game",invalid_start_selection_preserves_game},
        {"camera matches right-handed editor and asymmetric loot placement",camera_matches_right_handed_editor},
        {"player strafe and turning follow screen right",player_controls_follow_screen_right}
    };
    int failures=0;
    for(unsigned i=0;i<sizeof(cases)/sizeof(cases[0]);++i) {
        bool passed=cases[i].run(); printf("%s %s\n",passed?"PASS":"FAIL",cases[i].name); failures+=!passed;
    }
    return failures?1:0;
}
