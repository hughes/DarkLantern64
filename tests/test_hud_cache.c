/* Command-stream test: replay recorded blocks as nested text operations and
 * compare every original label, position and ordering. SDK source inspection
 * separately establishes that glyph commands retain only persistent font data. */
#include "game.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define assertf(c,...) do{if(!(c)){fprintf(stderr,__VA_ARGS__);abort();}}while(0)
#define CHECK(c) do{if(!(c)){fprintf(stderr,"%s:%d: %s\n",__FILE__,__LINE__,#c);return 1;}}while(0)
typedef struct rspq_block_s rspq_block_t;
typedef struct {float x,y;char text[128];rspq_block_t *child;} Command;
struct rspq_block_s {int count;Command commands[12];};
static rspq_block_t *recording,*alive[32];
static Command rendered[16];
static unsigned rendered_count,created,freed,text_calls,live_count;
static void rspq_block_begin(void){
    assertf(!recording,"nested recording");recording=calloc(1,sizeof *recording);
    assertf(recording,"allocation");
    for(unsigned i=0;i<32;++i)if(!alive[i]){alive[i]=recording;break;}
    ++created;++live_count;
}
static rspq_block_t *rspq_block_end(void){rspq_block_t *result=recording;recording=NULL;return result;}
static void rspq_block_run(rspq_block_t *block){
    assertf(block,"null block");
    if(recording){
        assertf(recording->count<12,"recording capacity");
        recording->commands[recording->count++]=(Command){.child=block};return;
    }
    for(int i=0;i<block->count;++i){
        Command command=block->commands[i];
        if(command.child)rspq_block_run(command.child);
        else{assertf(rendered_count<16,"replay capacity");rendered[rendered_count++]=command;}
    }
}
static void rspq_block_free(rspq_block_t *block){
    bool found=false;
    for(unsigned i=0;i<32;++i)if(alive[i]){
        if(alive[i]==block){alive[i]=NULL;found=true;continue;}
        for(int c=0;c<alive[i]->count;++c)
            assertf(alive[i]->commands[c].child!=block,"free referenced child before composite");
    }
    assertf(found,"double free");free(block);++freed;--live_count;
}
static void rdpq_text_print(const void *params,int font,float x,float y,const char *text){
    assertf(!params&&font==1&&recording,"unexpected print path");
    assertf(recording->count<12&&strlen(text)<128,"text capacity");
    Command *command=&recording->commands[recording->count++];command->x=x;command->y=y;
    strcpy(command->text,text);++text_calls;
}

#include "render_hud_cache.inc"

static int text_matches(int index,int x,int y,const char *text){
    CHECK(rendered[index].x==x&&rendered[index].y==y);
    CHECK(strcmp(rendered[index].text,text)==0);return 0;
}

static int scene(const char *title,const char *displayed){
    DlLevel level={.title=title};DlGame game={.level=&level};
    unsigned allocations=created;
    hud_cache_prepare(&game);CHECK(created-allocations==21&&live_count==21);
    hud_cache_prepare(&game);CHECK(created-allocations==21);
    unsigned prints=text_calls;
    const char *prompts[]={"Find the switch, open the gate, take the relic.",
        "Gate open. Find the gold relic and press A.","A: operate the gate switch",
        "A: take the relic","Spotted! Break sight and retreat into shadow."};
    for(int repeat=0;repeat<20;++repeat)for(int crouched=0;crouched<2;++crouched)for(int prompt=0;prompt<5;++prompt){
        game.crouched=crouched!=0;rendered_count=0;hud_cache_draw(&game,(HudPrompt)prompt);
        CHECK(rendered_count==8&&created-allocations==21&&text_calls==prints);
        CHECK(text_matches(0,9,12,"^01D A R K L A N T E R N  6 4")==0);
        CHECK(text_matches(1,9,22,displayed)==0);
        CHECK(text_matches(2,9,204,"LIGHT")==0&&text_matches(3,138,204,"ALERT")==0);
        CHECK(text_matches(4,263,204,crouched?"^01CROUCH":"STAND")==0);
        CHECK(text_matches(5,9,215,prompts[prompt])==0);
        CHECK(text_matches(6,9,226,"^02Stick: look  C: move/strafe  Z: crouch")==0);
        CHECK(text_matches(7,9,236,"^02A: use B: noise L: jump R: reset START: debug")==0);
    }
    for(int caught=0;caught<2;++caught){
        rendered_count=0;hud_cache_result(caught!=0);CHECK(rendered_count==3);
        CHECK(text_matches(0,66,99,caught?"^01CAUGHT BY THE WATCH":"^01RELIC ACQUIRED")==0);
        CHECK(text_matches(1,54,114,caught?"Use shadow, quiet steps and distractions.":"A quiet theft. The first heist is complete.")==0);
        CHECK(text_matches(2,107,130,"^02R: try again")==0);
    }
    hud_cache_release();CHECK(!hud_cache.level&&live_count==0&&created==freed);
    hud_cache_release();CHECK(created==freed);return 0;
}

int main(void){
    CHECK(scene("Guard ^01$FF\nWorkshop\t","Guard ^^01$$FF Workshop ")==0);
    CHECK(scene("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuv")==0);
    puts("HUD cache: 400 frame replays preserve exact text/order/positions; terminal variants, title escaping/truncation and scene lifetimes passed");
    return 0;
}
