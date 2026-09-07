#include "game.h"
#include "demo_level.h"
#include <math.h>
#include <stdio.h>

#define FRAME_DT (1.0f / 60.0f)
#define PI 3.14159265358979323846f

typedef struct {
    DlGame game;
    float peak_awareness, minimum_guard_distance, maximum_player_height;
    unsigned visible_frames, heard_frames;
    bool reached_step[4];
} Playtest;

static float distance(DlVec3 a, DlVec3 b) {
    float x=b.x-a.x, y=b.y-a.y, z=b.z-a.z;
    return sqrtf(x*x+y*y+z*z);
}

static bool frame(Playtest *test, DlInput input) {
    dl_game_update(&test->game,&input,FRAME_DT);
    DlGame *g=&test->game;
    test->peak_awareness=fmaxf(test->peak_awareness,g->enemies[0].awareness);
    test->minimum_guard_distance=fminf(test->minimum_guard_distance,distance(g->player,g->enemies[0].position));
    test->maximum_player_height=fmaxf(test->maximum_player_height,g->player.y);
    test->visible_frames+=g->enemies[0].sees_player;
    test->heard_frames+=g->enemies[0].heard_sound;
    for(int i=0;i<4;++i)
        if(g->grounded && fabsf(g->player.y-.2f*(i+1))<.002f) test->reached_step[i]=true;
    if(g->caught) {
        fprintf(stderr,"Captured at %.2fs: player (%.2f,%.2f,%.2f), guard (%.2f,%.2f,%.2f)\n",
            g->elapsed,g->player.x,g->player.y,g->player.z,g->enemies[0].position.x,g->enemies[0].position.y,g->enemies[0].position.z);
        return false;
    }
    if(!dl_position_clear(g,g->player,.18f,g->crouched?.95f:1.65f) ||
       !dl_position_clear(g,g->enemies[0].position,.20f,1.65f)) {
        fprintf(stderr,"Actor entered solid geometry at %.2fs.\n",g->elapsed); return false;
    }
    return true;
}

static bool walk_to(Playtest *test,DlVec3 destination) {
    /* Ordinary input only: the actual core owns movement, support, and AI. */
    for(int i=0;i<60*30;++i) {
        float dx=destination.x-test->game.player.x, dz=destination.z-test->game.player.z;
        if(dx*dx+dz*dz<.08f*.08f && fabsf(destination.y-test->game.player.y)<.12f) {
            printf("Reached (%.2f,%.2f,%.2f) at %.2fs; guard (%.2f,%.2f,%.2f), %s, awareness %.3f\n",
                destination.x,destination.y,destination.z,test->game.elapsed,
                test->game.enemies[0].position.x,test->game.enemies[0].position.y,test->game.enemies[0].position.z,
                dl_guard_state_name(test->game.enemies[0].state),test->game.enemies[0].awareness);
            return true;
        }
        float error=atan2f(dx,dz)-test->game.yaw;
        while(error>PI) error-=2*PI;
        while(error<-PI) error+=2*PI;
        float turn=fmaxf(-1,fminf(1,-error/(2.3f*FRAME_DT)));
        DlInput input={.crouch=true,.turn=turn,.forward=fabsf(error)<.10f?1:0};
        if(!frame(test,input)) return false;
    }
    fprintf(stderr,"Could not reach (%.2f,%.2f,%.2f); stopped at (%.2f,%.2f,%.2f).\n",
        destination.x,destination.y,destination.z,test->game.player.x,test->game.player.y,test->game.player.z);
    return false;
}

int main(void) {
    Playtest test={.minimum_guard_distance=INFINITY};
    dl_game_init(&test.game,&dl_demo_level);
    if(dl_demo_level.version!=2 || dl_demo_level.mesh_count<1 || dl_demo_level.collider_count<1 || test.game.door_open) {
        fprintf(stderr,"Expected the authored full-3D scene with a closed linked door.\n"); return 1;
    }
    /* This is the same route and steering tolerance used by the optional ROM replay.
     * Each stage consumes one ordinary crouched frame, including use at stages 1/6. */
    DlVec3 route[]={
        {-2,0,2}, {dl_demo_level.control.x,0,dl_demo_level.control.z},
        {1.2f,0,0}, {2.5f,0,2}, {5,0,1.7f},
        {5,.8f,4.2f}, {dl_demo_level.objective.x,.8f,dl_demo_level.objective.z}
    };
    DlVec3 before_door={-1,1,0}, after_door={1,1,0};
    if(dl_line_of_sight(&test.game,before_door,after_door)) {
        fprintf(stderr,"Closed door did not obstruct its opening.\n"); return 1;
    }
    for(unsigned stage=0;stage<sizeof(route)/sizeof(route[0]);++stage) {
        if(!walk_to(&test,route[stage])) return 1;
        if(!frame(&test,(DlInput){.crouch=true,.use=stage==1 || stage==6})) return 1;
        if(stage==1) {
            if(!test.game.door_open || test.game.event!=DL_EVENT_DOOR ||
               !dl_line_of_sight(&test.game,before_door,after_door)) {
                fprintf(stderr,"Authored control did not open its door.\n"); return 1;
            }
            printf("Opened door at %.2fs; guard heard it: %s\n",test.game.elapsed,
                test.game.enemies[0].heard_sound?"yes":"no");
        }
    }
    if(!test.game.complete || test.game.caught || test.game.event!=DL_EVENT_OBJECTIVE ||
       test.game.player.y<.75f || !test.reached_step[0] || !test.reached_step[1] ||
       !test.reached_step[2] || !test.reached_step[3]) {
        fprintf(stderr,"Expected safe objective completion after physically climbing all four steps.\n"); return 1;
    }
    printf("PASS input-only first-room control -> door -> four stairs -> raised objective: %.2fs, "
        "peak awareness %.3f, minimum guard distance %.2f, maximum feet height %.2f, visible frames %u, heard frames %u\n",
        test.game.elapsed,test.peak_awareness,test.minimum_guard_distance,test.maximum_player_height,
        test.visible_frames,test.heard_frames);
    return 0;
}
