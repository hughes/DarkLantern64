#include "game.h"
#include <float.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define CHECK(c) do{if(!(c)){fprintf(stderr,"%s:%d: %s\n",__FILE__,__LINE__,#c);return 1;}}while(0)
static uint32_t seed=UINT32_C(0xbaff064);
static float random_float(float lo,float hi){seed=seed*1664525u+1013904223u;return lo+(hi-lo)*(seed>>8)*(1.0f/16777216);}
static DlVec3 point(float range){return (DlVec3){random_float(-range,range),random_float(-5,9),random_float(-range,range)};}
static int same_game(DlGame a,DlGame b){
    a.level=b.level=NULL;
    for(int i=0;i<DL_EVENT_CAPACITY;++i){
        DlWorldEvent x=a.events[i],y=b.events[i];
        CHECK(x.kind==y.kind&&memcmp(&x.position,&y.position,sizeof x.position)==0&&x.time==y.time&&x.strength==y.strength&&
            x.actor==y.actor&&x.detail==y.detail&&x.previous==y.previous);
    }
    memset(a.events,0,sizeof a.events);memset(b.events,0,sizeof b.events);
    CHECK(memcmp(&a,&b,sizeof a)==0);return 0;
}
static int randomized_queries(void){
    DlCollider boxes[64];DlFloorTriangle floors[16];static DlQueryCache cache;
    for(int i=0;i<64;++i)boxes[i]=(DlCollider){.center=point(24),
        .half_size={random_float(.01f,3),random_float(.01f,3),random_float(.01f,3)},.yaw=random_float(-3.14159f,3.14159f),.door=i%11==0};
    for(int i=0;i<8;++i){
        float x=random_float(-20,20),z=random_float(-20,20),y=random_float(-2,3),w=random_float(.01f,5),h=random_float(.01f,5);
        DlVec3 a={x,y,z},b={x+w,y+w*.25f,z},c={x+w,y+w*.25f-h*.1f,z+h},d={x,y-h*.1f,z+h};
        floors[i*2]=(DlFloorTriangle){{a,b,c}};floors[i*2+1]=(DlFloorTriangle){{a,c,d}};
    }
    DlLevel original={.colliders=boxes,.collider_count=64,.floors=floors,.floor_count=16};
    CHECK(dl_game_prepare_query_cache(&cache,&original));DlLevel prepared=original;prepared.query_cache=&cache;
    DlGame a={.level=&original},b={.level=&prepared};
    for(int i=0;i<30000;++i){
        DlVec3 from=point(32),to=point(32);float radius=random_float(.00001f,.5f),height=random_float(.01f,2);
        a.door_open=b.door_open=i%2!=0;
        CHECK(dl_position_clear(&a,from,radius,height)==dl_position_clear(&b,from,radius,height));
        CHECK(dl_line_of_sight(&a,from,to)==dl_line_of_sight(&b,from,to));
    }
    /* Identical pointers with a mismatched count must use the scalar path. */
    cache.collider_count=63;cache.floor_count=15;
    for(int i=0;i<1000;++i){DlVec3 from=point(32),to=point(32);
        CHECK(dl_position_clear(&a,from,.18f,1.65f)==dl_position_clear(&b,from,.18f,1.65f));
        CHECK(dl_line_of_sight(&a,from,to)==dl_line_of_sight(&b,from,to));}
    return 0;
}
static int grazing_and_malformed(void){
    static DlQueryCache cache;
    for(int i=0;i<3000;++i){
        float yaw=random_float(-3.14159f,3.14159f),c=cosf(yaw),s=sinf(yaw);
        DlCollider box={.center=point(32),.half_size={random_float(.01f,3),2,random_float(.01f,3)},.yaw=yaw};
        DlLevel original={.colliders=&box,.collider_count=1};CHECK(dl_game_prepare_query_cache(&cache,&original));
        DlLevel prepared=original;prepared.query_cache=&cache;DlGame a={.level=&original},b={.level=&prepared};
        float x=box.half_size.x+.18f,z=random_float(-box.half_size.z,box.half_size.z);
        for(int edge=-1;edge<=1;++edge){
            float xx=edge<0?nextafterf(x,-INFINITY):edge>0?nextafterf(x,INFINITY):x;
            DlVec3 p={box.center.x+c*xx+s*z,box.center.y,box.center.z-s*xx+c*z};
            CHECK(dl_position_clear(&a,p,.18f,1.65f)==dl_position_clear(&b,p,.18f,1.65f));
            DlVec3 q={box.center.x+c*(-box.half_size.x)+s*z,p.y,box.center.z-s*(-box.half_size.x)+c*z};
            CHECK(dl_line_of_sight(&a,p,q)==dl_line_of_sight(&b,p,q));
        }
        DlVec3 far={1e10f,box.center.y,1e10f},near={box.center.x+5,box.center.y,box.center.z+5};
        CHECK(dl_line_of_sight(&a,far,near)==dl_line_of_sight(&b,far,near));
    }
    DlCollider bad={.center={0,1,0},.half_size={1,1,1},.yaw=NAN};
    DlFloorTriangle floors[]={{{{0,0,0},{1,0,0},{0,0,1}}},{{{0,0,0},{0,1,0},{0,0,1}}},{{{0,NAN,0},{1,0,0},{0,0,1}}}};
    DlLevel original={.colliders=&bad,.collider_count=1,.floors=floors,.floor_count=3};
    CHECK(dl_game_prepare_query_cache(&cache,&original));DlLevel prepared=original;prepared.query_cache=&cache;
    DlGame a={.level=&original},b={.level=&prepared};
    CHECK(dl_position_clear(&a,(DlVec3){.3f,-.5f,.3f},.18f,1.65f)==dl_position_clear(&b,(DlVec3){.3f,-.5f,.3f},.18f,1.65f));
    CHECK(dl_line_of_sight(&a,(DlVec3){-2,1,0},(DlVec3){2,1,0})==dl_line_of_sight(&b,(DlVec3){-2,1,0},(DlVec3){2,1,0}));
    return 0;
}
static int exact_movement_traces(void){
    DlCollider boxes[DL_QUERY_CACHE_COLLIDERS];DlFloorTriangle ramp[]={
        {{{-3,0,-1},{-1,0,-1},{-1,1,3}}},{{{-3,0,-1},{-1,1,3},{-3,1,3}}}};
    boxes[0]=(DlCollider){.center={0,-.25f,0},.half_size={15,.25f,15}};
    boxes[1]=(DlCollider){.center={3,1.5f,0},.half_size={.08f,1.5f,4},.yaw=.785398163f};
    for(int i=2;i<DL_QUERY_CACHE_COLLIDERS;++i)boxes[i]=(DlCollider){.center={50+i*.8f,1,50+i*.5f},.half_size={.2f,1,.2f},.yaw=i*.13f};
    boxes[2]=(DlCollider){.center={0,1,8},.half_size={1,1,.08f},.door=true};
    DlEnemyDef guard={.id="guard",.type=DL_ENEMY_WATCHMAN,.behavior=DL_BEHAVIOR_SENTRY,.spawn={10,0,10},.speed=1,.hearing_range=20};
    DlLevel original={.colliders=boxes,.collider_count=DL_QUERY_CACHE_COLLIDERS,.floors=ramp,.floor_count=2,
        .spawn={-2,0,-2},.enemies=&guard,.enemy_count=1,.control={0,1,8},.objective={100,1,100}};
    static DlQueryCache cache;CHECK(dl_game_prepare_query_cache(&cache,&original));
    DlLevel prepared=original;prepared.query_cache=&cache;DlGame a,b;dl_game_init(&a,&original);b=a;b.level=&prepared;
    bool airborne=false,high=false,slide=false;
    for(int i=0;i<1200;++i){
        DlInput in={0};
        if(i<180)in.forward=1;else if(i<240)in.strafe=-1;else if(i<360)in.forward=-1;else if(i<540)in.strafe=-1;
        else{in.forward=random_float(-1,1);in.strafe=random_float(-1,1);in.turn=random_float(-.2f,.2f);in.crouch=i%70<20;in.noise=i%97==0;in.use=i%83==0;}
        in.jump=i==120||i%131==0;
        if(i%100==0)a.door_open=b.door_open=!a.door_open;
        DlVec3 before=a.player;dl_game_update(&a,&in,1.0f/60);dl_game_update(&b,&in,1.0f/60);
        CHECK(same_game(a,b)==0);airborne|=!a.grounded;high|=a.player.y>.8f;
        slide|=i>=360&&i<540&&a.player.x>0&&a.player.z!=before.z;
    }
    CHECK(airborne&&high&&slide);return 0;
}
static int incremental_index_and_dense_traces(void){
    DlCollider boxes[DL_QUERY_CACHE_COLLIDERS];DlFloorTriangle floors[DL_QUERY_CACHE_FLOORS];
    for(int i=0;i<DL_QUERY_CACHE_COLLIDERS;++i){
        /* Interleaved remote/local source order defeats grouping by input order
         * and stresses newly entered later contacts during depenetration. */
        float remote=i%2?100:0;
        boxes[i]=(DlCollider){.center={remote+random_float(-5,5),random_float(-.5f,2),remote+random_float(-5,5)},
            .half_size={random_float(.01f,.7f),random_float(.01f,1.5f),random_float(.01f,.7f)},.yaw=random_float(-3.1f,3.1f),.door=i%17==0};
    }
    boxes[0]=(DlCollider){.center={0,-.25f,0},.half_size={20,.25f,20}};
    for(int i=0;i<DL_QUERY_CACHE_FLOORS;++i){
        float x=random_float(-8,8),z=random_float(-8,8),y=random_float(-1,2),width=random_float(.05f,2);
        floors[i]=(DlFloorTriangle){{{x,y,z},{x+width,y+width*.25f,z},{x,y,z+width}}};
    }
    DlLevel original={.colliders=boxes,.collider_count=DL_QUERY_CACHE_COLLIDERS,.floors=floors,.floor_count=DL_QUERY_CACHE_FLOORS,
        .spawn={0,0,0},.control={100,100,100},.objective={100,100,100}};
    static DlQueryCache cache;CHECK(dl_game_prepare_query_cache(&cache,&original));
    DlLevel prepared=original;prepared.query_cache=&cache;CHECK(dl_game_query_cache_ready(&prepared));
    dl_query_cache_index_begin(&cache);CHECK(!dl_game_query_cache_ready(&prepared));
    DlGame a={.level=&original},b={.level=&prepared};
    CHECK(dl_position_clear(&a,(DlVec3){0,.2f,0},.18f,1.65f)==dl_position_clear(&b,(DlVec3){0,.2f,0},.18f,1.65f));
    CHECK(dl_line_of_sight(&a,(DlVec3){-6,1,-6},(DlVec3){6,1,6})==dl_line_of_sight(&b,(DlVec3){-6,1,-6},(DlVec3){6,1,6}));
    unsigned records=0;
    while(!dl_query_cache_index_step(&cache)){CHECK(!dl_game_query_cache_ready(&prepared));CHECK(++records<DL_QUERY_CACHE_CONTACTS*7);}
    CHECK(dl_game_query_cache_ready(&prepared)&&cache.node_count==DL_QUERY_CACHE_CONTACTS*2-1);
    for(int i=0;i<DL_QUERY_CACHE_FLOORS;++i){
        DlVec3 from=floors[i].vertices[0];
        for(int edge=1;edge<3;++edge){DlVec3 to=floors[i].vertices[edge];
            CHECK(dl_line_of_sight(&a,from,to)==dl_line_of_sight(&b,from,to));}
    }
    for(int i=0;i<10000;++i){DlVec3 from=point(10),to=point(10);float radius=random_float(.01f,2),height=random_float(.01f,4);
        CHECK(dl_position_clear(&a,from,radius,height)==dl_position_clear(&b,from,radius,height));
        CHECK(dl_line_of_sight(&a,from,to)==dl_line_of_sight(&b,from,to));}
    for(int trial=0;trial<24;++trial){
        dl_game_init(&a,&original);a.player=(DlVec3){random_float(-5,5),random_float(-.5f,2),random_float(-5,5)};
        a.grounded=trial%2!=0;a.yaw=random_float(-3,3);b=a;b.level=&prepared;
        for(int tick=0;tick<80;++tick){DlInput input={.forward=random_float(-1,1),.strafe=random_float(-1,1),
                .turn=random_float(-1,1),.crouch=tick%19<3,.jump=tick%23==0};
            a.door_open=b.door_open=tick%31<16;
            float dt=tick%7==0?.037f:1.0f/60;
            dl_game_update(&a,&input,dt);dl_game_update(&b,&input,dt);CHECK(same_game(a,b)==0);
        }
    }
    return 0;
}
static int long_diagonal_lighting_rays(void){
    DlCollider boxes[DL_QUERY_CACHE_COLLIDERS];DlFloorTriangle floors[DL_QUERY_CACHE_FLOORS];DlLight lights[8];
    for(int i=0;i<DL_QUERY_CACHE_COLLIDERS;++i)
        boxes[i]=(DlCollider){.center={random_float(-24,24),random_float(-2,8),random_float(-24,24)},
            .half_size={random_float(.001f,2),random_float(.001f,3),random_float(.001f,2)},
            .yaw=random_float(-3.14f,3.14f),.door=i%31==0};
    for(int i=0;i<DL_QUERY_CACHE_FLOORS;++i){float x=random_float(-24,24),z=random_float(-24,24),y=random_float(-2,6);
        float width=random_float(.01f,8),depth=random_float(.01f,8);
        floors[i]=(DlFloorTriangle){{{x,y,z},{x+width,y+width*.2f,z},{x,y+depth*.1f,z+depth}}};}
    for(int i=0;i<8;++i)lights[i]=(DlLight){.position=point(24),.radius=40,.intensity=.6f,.color={.3f,.6f,.8f}};
    DlLevel original={.colliders=boxes,.collider_count=DL_QUERY_CACHE_COLLIDERS,.floors=floors,.floor_count=DL_QUERY_CACHE_FLOORS,
        .lights=lights,.light_count=8,.environment={.enabled=true,.ambient={.1f,.11f,.14f},
            .moon_direction={-.4f,.8f,.3f},.moon_intensity=.08f,.moon_color={.48f,.58f,.85f}}};
    static DlQueryCache cache;CHECK(dl_game_prepare_query_cache(&cache,&original));
    DlLevel prepared=original;prepared.query_cache=&cache;DlGame a={.level=&original},b={.level=&prepared};
    for(int i=0;i<12000;++i){DlVec3 from=point(32),to={from.x-27.135f,from.y+54.27f,from.z+20.351f};
        a.door_open=b.door_open=i%2!=0;
        CHECK(dl_line_of_sight(&a,from,to)==dl_line_of_sight(&b,from,to));
        CHECK(dl_visibility(&a,from)==dl_visibility(&b,from));
        DlVec3 normal=point(1),scalar=dl_surface_light(&a,from,normal),indexed=dl_surface_light(&b,from,normal);
        CHECK(memcmp(&scalar,&indexed,sizeof scalar)==0);
    }
    /* Very large finite endpoints and exact corner/edge rays must survive the
     * broad phase's rounding/overflow allowance before the original slabs. */
    const float ranges[]={64,1e5f,1e10f,1e30f};
    for(int box=0;box<DL_QUERY_CACHE_COLLIDERS;++box){float c=cosf(boxes[box].yaw),s=sinf(boxes[box].yaw);
        DlVec3 corner={boxes[box].center.x+c*boxes[box].half_size.x+s*boxes[box].half_size.z,
            boxes[box].center.y+boxes[box].half_size.y,
            boxes[box].center.z-s*boxes[box].half_size.x+c*boxes[box].half_size.z};
        for(unsigned range=0;range<sizeof ranges/sizeof ranges[0];++range){float distance=ranges[range];
            DlVec3 from={corner.x-distance*.4f,corner.y-distance*.8f,corner.z-distance*.3f};
            DlVec3 to={corner.x+distance*.4f,corner.y+distance*.8f,corner.z+distance*.3f};
            CHECK(dl_line_of_sight(&a,from,to)==dl_line_of_sight(&b,from,to));
        }
    }
    dl_game_init(&a,&original);b=a;b.level=&prepared;
    for(int tick=0;tick<480;++tick){DlInput input={.forward=random_float(-1,1),.strafe=random_float(-1,1),
            .turn=random_float(-1,1),.crouch=tick%17<3,.jump=tick%29==0};
        a.door_open=b.door_open=tick%37<18;dl_game_update(&a,&input,1.0f/60);dl_game_update(&b,&input,1.0f/60);
        CHECK(same_game(a,b)==0);
    }
    return 0;
}
static int linked_door_loading_barrier(void){
    const DlCollider boxes[]={
        {.center={0,-.1f,0},.half_size={2,.1f,2}},
        {.center={0,1,0},.half_size={.5f,1,.04f},.door=true},
        {.center={0,1,0},.half_size={.6f,1,.04f}}};
    int16_t links[]={-1,0,0};
    const DlObjectDef doors[]={
        {.id="door",.kind=DL_OBJECT_DOOR,.position={0,1,0},.radius=1.5f},
        {.id="other",.kind=DL_OBJECT_DOOR,.position={0,1,0},.radius=0}};
    DlLevel original={.colliders=boxes,.collider_count=3,.collider_objects=links,
        .objects=doors,.object_count=2,.spawn={0,0,1}};
    static DlQueryCache cache;CHECK(dl_game_prepare_query_cache(&cache,&original));
    DlLevel prepared=original;prepared.query_cache=&cache;DlGame games[2];
    dl_game_init(&games[0],&original);dl_game_init(&games[1],&prepared);
    for(unsigned mode=0;mode<2;++mode){DlGame *game=&games[mode];game->door_open=true;game->open_objects=1;
        CHECK(!dl_line_of_sight(game,dl_player_eye(game),doors[0].position));
        CHECK(!dl_position_clear(game,(DlVec3){0,0,0},.18f,1.65f));
        dl_game_update(game,&(DlInput){.use=true},1.0f/60);
        CHECK(!game->door_open&&game->object_revision==1&&game->event_count==1);
        dl_game_update(game,&(DlInput){.use=true},1.0f/60);
        CHECK(game->door_open&&game->object_revision==2&&game->event_count==1);
        CHECK(!dl_line_of_sight(game,dl_player_eye(game),doors[0].position));
    }
    CHECK(same_game(games[0],games[1])==0);
    for(int tick=0;tick<120;++tick){DlInput walk={.forward=-1};
        dl_game_update(&games[0],&walk,1.0f/60);dl_game_update(&games[1],&walk,1.0f/60);
        CHECK(same_game(games[0],games[1])==0);
    }
    CHECK(games[0].player.z>=.219f&&games[0].player.z<.3f);
    /* Association with a different door, or no association, cannot make an
     * ordinary obstruction transparent to this door's direct interaction. */
    for(int link=1;link>=-1;link-=2){links[2]=(int16_t)link;
        for(unsigned mode=0;mode<2;++mode){DlGame *game=&games[mode];game->player=(DlVec3){0,0,1};
            uint32_t revision=game->object_revision;dl_game_update(game,&(DlInput){.use=true},1.0f/60);
            CHECK(game->door_open&&game->object_revision==revision&&game->event_count==0);
        }
        CHECK(same_game(games[0],games[1])==0);
    }
    return 0;
}
int main(void){
    CHECK(randomized_queries()==0);CHECK(grazing_and_malformed()==0);CHECK(exact_movement_traces()==0);CHECK(incremental_index_and_dense_traces()==0);
    CHECK(long_diagonal_lighting_rays()==0);CHECK(linked_door_loading_barrier()==0);
    puts("cached collision/BVH: exact random/grazing/long diagonal rays, lighting, incremental construction, dense contact order, malformed fallback, slopes, jumps, sliding, doors, hearing and full-state traces passed");return 0;
}
