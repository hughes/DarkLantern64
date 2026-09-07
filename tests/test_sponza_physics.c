/* Generated authored colliders, unchanged production gameplay implementation. */
#include "game.h"
#include "sponza_physics_fixture.h"
#include <math.h>
#include <stdio.h>
#include <string.h>

#define PI 3.14159265358979323846f
#define CHECK(c) do { if (!(c)) { fprintf(stderr,"%s:%d: %s\n",__FILE__,__LINE__,#c); return false; } } while (0)
static unsigned samples, traversal_count, airborne_samples, los_checks, clearance_checks;
static float minimum_y=100, maximum_y=-100;
static float mission_seconds;
static bool mission_ordinary_guards;

static DlLevel level(void) {
    return (DlLevel){.version=2,.title="Authored Sponza collision",
        .colliders=fixture_boxes,.collider_count=(int)(sizeof(fixture_boxes)/sizeof(*fixture_boxes)),
        .spawn={18.2f,0,0},.control={100,100,100},.objective={100,100,100}};
}
static float horizontal(DlVec3 a,DlVec3 b) {
    float x=a.x-b.x,z=a.z-b.z;return sqrtf(x*x+z*z);
}
static bool actor_valid(const DlGame *g,DlVec3 p,float radius,float height) {
    ++samples;minimum_y=fminf(minimum_y,p.y);maximum_y=fmaxf(maximum_y,p.y);
    CHECK(isfinite(p.x)&&isfinite(p.y)&&isfinite(p.z));
    CHECK(p.y>=-.002f && p.y<=5.302f);
    CHECK(dl_position_clear(g,p,radius,height));
    return true;
}
static bool support(const DlGame *g,DlVec3 p,float radius,float height) {
    p.y-=.02f;return !dl_position_clear(g,p,radius,height);
}
static bool traverse(const DlVec3 *authored,const char *side,bool descending,int actor,float dt) {
    DlVec3 route[4];for(int i=0;i<4;++i)route[i]=authored[descending?3-i:i];
    DlLevel l=level();DlEnemyDef definition={0};
    if(actor==2) {
        definition=(DlEnemyDef){.id="stairs-watchman",.spawn=route[0],.patrol=route,.patrol_count=4,.speed=1.1f};
        l.enemies=&definition;l.enemy_count=1;
    } else l.spawn=route[0];
    DlGame g;dl_game_init(&g,&l);
    float radius=actor==2?.20f:.18f,height=actor==1?.95f:1.65f;
    unsigned steps=0;int target=1;float elapsed=0;
    while(elapsed<40 && target<4) {
        DlVec3 p=actor==2?g.enemies[0].position:g.player;
        CHECK(actor_valid(&g,p,radius,height));
        for(int i=1;i<=22;++i)if(fabsf(p.y-i*(5.3f/22))<.002f)steps|=1u<<(i-1);
        /* Guard arrival uses the production 0.12 m tolerance. Observe its
         * patrol transition instead of imposing a tighter second controller. */
        bool reached=actor==2 ? g.enemies[0].patrol_index==(target+1)%4 :
            horizontal(p,route[target])<.10f && fabsf(p.y-route[target].y)<.08f;
        if(reached) {
            CHECK(horizontal(p,route[target])<.12f+1.1f*dt+.002f);
            CHECK(fabsf(p.y-route[target].y)<.08f);
            ++target;if(target==4)break;
        }
        DlInput in={.crouch=actor==1};
        if(actor!=2) {
            float dx=route[target].x-p.x,dz=route[target].z-p.z;
            float error=atan2f(dx,dz)-g.yaw;
            while(error>PI)error-=2*PI;
            while(error<-PI)error+=2*PI;
            in.turn=fmaxf(-1,fminf(1,-error/(2.3f*dt)));
            in.forward=fabsf(error)<.08f?fminf(1,horizontal(p,route[target])/((actor==1?1:2.1f)*dt)):0;
        }
        dl_game_update(&g,&in,dt);elapsed+=dt;
        if(actor==2?g.enemies[0].vertical_velocity!=0:!g.grounded)++airborne_samples;
    }
    DlVec3 p=actor==2?g.enemies[0].position:g.player;
    if(target!=4)fprintf(stderr,"%s %s actor=%d dt=%.5f stalled at %.4f %.4f %.4f target=%d patrol=%d\n",
        side,descending?"down":"up",actor,dt,p.x,p.y,p.z,target,g.enemies[0].patrol_index);
    CHECK(target==4);
    /* Arrival can happen during the final ordinary step down. Hold the
     * endpoint for half a second and require gravity to settle on its floor. */
    if(actor==2) { definition.behavior=DL_BEHAVIOR_SENTRY;definition.spawn=route[3]; }
    for(int i=0;i<(int)ceilf(.5f/dt);++i) {
        dl_game_update(&g,&(DlInput){.crouch=actor==1},dt);
        CHECK(actor_valid(&g,actor==2?g.enemies[0].position:g.player,radius,height));
    }
    p=actor==2?g.enemies[0].position:g.player;
    CHECK(actor_valid(&g,p,radius,height));
    if(!support(&g,p,radius,height))fprintf(stderr,"%s %s actor=%d dt=%.5f unsupported endpoint %.4f %.4f %.4f\n",
        side,descending?"down":"up",actor,dt,p.x,p.y,p.z);
    CHECK(support(&g,p,radius,height));
    CHECK(fabsf(p.y-route[3].y)<.002f);
    if(!descending && dt<.04f)CHECK(steps==((1u<<22)-1));
    ++traversal_count;return true;
}
static bool floors_clearance_and_sight(void) {
    DlLevel l=level();DlGame g;dl_game_init(&g,&l);
    for(int sign=-1;sign<=1;sign+=2)for(int upper=0;upper<2;++upper) {
        l.spawn=(DlVec3){2,upper?5.3f:0,sign*4.7f};dl_game_init(&g,&l);
        CHECK(g.grounded && support(&g,g.player,.18f,1.65f));
        for(int i=0;i<60;++i)dl_game_update(&g,&(DlInput){0},1.0f/60);
        CHECK(fabsf(g.player.y-l.spawn.y)<.002f && g.grounded);
        CHECK(dl_position_clear(&g,g.player,.18f,1.65f));
        /* The matching upper slab/ceiling bounds the available headroom. */
        CHECK(!dl_position_clear(&g,g.player,.18f,upper?5.21f:5.11f));
        ++clearance_checks;
        DlVec3 below={2,1.5f,sign*4.7f},above={2,6.8f,sign*4.7f};
        CHECK(!dl_line_of_sight(&g,below,above));CHECK(!dl_line_of_sight(&g,above,below));los_checks+=2;
    }
    DlVec3 below={2,1.5f,2.5f},above={2,6.8f,-3.3f};
    CHECK(dl_line_of_sight(&g,below,above));CHECK(dl_line_of_sight(&g,above,below));los_checks+=2;
    DlEnemyDef sentry={.id="upper-lookout",.behavior=DL_BEHAVIOR_SENTRY,
        .spawn={2,5.3f,-3.3f},.sight_range=20};
    l.enemies=&sentry;l.enemy_count=1;l.spawn=(DlVec3){2,0,2.5f};
    l.environment=(DlEnvironment){.enabled=true,.ambient={1,1,1}};
    dl_game_init(&g,&l);dl_game_update(&g,&(DlInput){0},1.0f/60);
    CHECK(g.enemies[0].sees_player);++los_checks;
    /* Same floor heights, solid gallery slab in the actual ray. */
    sentry.spawn=(DlVec3){2,5.3f,-4.7f};l.spawn=(DlVec3){2,0,-.7f};
    dl_game_init(&g,&l);dl_game_update(&g,&(DlInput){0},1.0f/60);
    /* Keep the lower actor inside the guard's 55-degree view cone, so
     * this negative case cannot pass merely because it fails the FOV test. */
    CHECK(4/sqrtf(4*4+5.3f*5.3f)>.573576f);
    CHECK(!dl_line_of_sight(&g,dl_player_eye(&g),(DlVec3){2,6.8f,-4.7f}));
    CHECK(!g.enemies[0].sees_player);++los_checks;
    return true;
}
static bool jump_ceiling(const DlVec3 *route) {
    DlLevel l=level();float sign=route[0].x>0?1:-1;
    l.spawn=(DlVec3){sign*11.55f,5.3f,sign*4.6f};DlGame g;dl_game_init(&g,&l);
    CHECK(g.grounded && dl_position_clear(&g,g.player,.18f,1.65f));
    float peak=g.player.y;
    for(int i=0;i<90;++i) {
        dl_game_update(&g,&(DlInput){.jump=i==0},1.0f/60);
        peak=fmaxf(peak,g.player.y);
        CHECK(dl_position_clear(&g,g.player,.18f,1.65f));
        /* At the top doorway the authored 7.4 m header is lower than
         * the 7.6 m stairwell ceiling and overlaps the actor radius. */
        CHECK(g.player.y+1.65f<=7.4001f);
    }
    CHECK(fabsf(peak-5.75f)<.002f);
    CHECK(g.grounded && fabsf(g.player.y-5.3f)<.002f);
    ++clearance_checks;return true;
}
#ifdef SPONZA_MISSION_AVAILABLE
static bool mission_walk(DlGame *g,DlVec3 destination) {
    for(int i=0;i<60*40;++i) {
        float dx=destination.x-g->player.x,dz=destination.z-g->player.z;
        if(horizontal(g->player,destination)<.10f && fabsf(g->player.y-destination.y)<.08f)return true;
        float error=atan2f(dx,dz)-g->yaw;
        while(error>PI)error-=2*PI;
        while(error<-PI)error+=2*PI;
        DlInput input={.crouch=true,.turn=fmaxf(-1,fminf(1,-error/(2.3f/60))),
            .forward=fabsf(error)<.08f?fminf(1,horizontal(g->player,destination)*60):0};
        dl_game_update(g,&input,1.0f/60);
        if(g->caught) {
            fprintf(stderr,"Mission caught at %.2fs player %.2f %.2f %.2f\n",g->elapsed,g->player.x,g->player.y,g->player.z);
            return false;
        }
        CHECK(actor_valid(g,g->player,.18f,.95f));
        for(int enemy=0;enemy<g->enemy_count;++enemy)
            CHECK(dl_position_clear(g,g->enemies[enemy].position,.20f,1.65f));
    }
    fprintf(stderr,"Mission stalled at %.2f %.2f %.2f toward %.2f %.2f %.2f\n",
        g->player.x,g->player.y,g->player.z,destination.x,destination.y,destination.z);
    return false;
}
static bool objective_loop(void) {
    DlLevel l=mission_level();DlGame g;dl_game_init(&g,&l);
    CHECK(!g.door_open && !g.complete);
    CHECK(mission_walk(&g,(DlVec3){10.4f,0,0}));
    CHECK(mission_walk(&g,(DlVec3){10.4f,0,3.4f}));
    for(int i=0;i<4;++i)CHECK(mission_walk(&g,route_east[i]));
    CHECK(mission_walk(&g,(DlVec3){10.2f,5.3f,4.7f}));
    CHECK(mission_walk(&g,(DlVec3){-10.2f,5.3f,4.7f}));
    CHECK(mission_walk(&g,(DlVec3){-10.2f,5.3f,-4.7f}));
    CHECK(mission_walk(&g,(DlVec3){l.control.x,5.3f,-4.7f}));
    dl_game_update(&g,&(DlInput){.crouch=true,.use=true},1.0f/60);
    CHECK(g.door_open && !g.complete);
    CHECK(mission_walk(&g,(DlVec3){-10.2f,5.3f,-4.7f}));
    for(int i=3;i>=0;--i)CHECK(mission_walk(&g,route_west[i]));
    CHECK(mission_walk(&g,(DlVec3){-10.2f,0,-3.4f}));
    CHECK(mission_walk(&g,(DlVec3){-10.2f,0,0}));
    CHECK(mission_walk(&g,(DlVec3){-13.6f,0,0}));
    dl_game_update(&g,&(DlInput){.crouch=true,.use=true},1.0f/60);
    CHECK(g.complete && g.door_open && !g.caught);
    mission_seconds=g.elapsed;mission_ordinary_guards=true;return true;
}
#endif
int main(void) {
    const float dt[]={1.0f/60,1.0f/30,.1f};
    for(int side=0;side<2;++side)for(int down=0;down<2;++down)
        for(int actor=0;actor<3;++actor)for(int rate=0;rate<3;++rate)
            if(!traverse(side?route_west:route_east,side?"west":"east",down,actor,dt[rate]))return 1;
    if(!floors_clearance_and_sight() || !jump_ceiling(route_east) || !jump_ceiling(route_west))return 1;
#ifdef SPONZA_MISSION_AVAILABLE
    if(!objective_loop())return 1;
#endif
    printf("{\"passed\":true,\"traversals\":%u,\"actor_samples\":%u,\"airborne_samples\":%u,"
        "\"minimum_feet_y\":%.4f,\"maximum_feet_y\":%.4f,\"los_checks\":%u,\"clearance_checks\":%u,"
        "\"actors\":[\"standing player\",\"crouched player\",\"patrol guard\"],"
        "\"update_rates_hz\":[60,30,10],\"steps_per_stair\":22,"
        "\"mission_ordinary_guards\":%s,\"mission_seconds\":%.3f}\n",
        traversal_count,samples,airborne_samples,minimum_y,maximum_y,los_checks,clearance_checks,
        mission_ordinary_guards?"true":"false",mission_seconds);
    return 0;
}
