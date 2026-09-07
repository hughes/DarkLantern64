/* Fixed HUD commands, shared by both renderers. The SDK's text_printn lays
 * out a paragraph on its stack; font_render_paragraph immediately emits glyph
 * rectangles and references the persistent font atlas's command block. Neither
 * paragraph nor input string is DMA-referenced, so recording retains no text
 * layout memory. Font 1 and its styles remain alive for the renderer lifetime.
 * Composite -> text -> font-atlas depth is 3, below RSPQ's nesting limit of 8. */
typedef enum {
    HUD_PROMPT_CLOSED,HUD_PROMPT_OPEN,HUD_PROMPT_SWITCH,
    HUD_PROMPT_RELIC,HUD_PROMPT_CHASE,HUD_PROMPT_COUNT
} HudPrompt;
static struct {
    const DlLevel *level;
    rspq_block_t *prefix,*suffix,*stance[2],*prompt[HUD_PROMPT_COUNT];
    rspq_block_t *composite[2][HUD_PROMPT_COUNT],*result[2];
} hud_cache;

/* Caller has waited for queued graphics before releasing a scene. Free
 * composites first because they reference the smaller shared text blocks. */
static void hud_cache_release(void){
    for(int stance=0;stance<2;++stance)for(int prompt=0;prompt<HUD_PROMPT_COUNT;++prompt)
        if(hud_cache.composite[stance][prompt])rspq_block_free(hud_cache.composite[stance][prompt]);
    if(hud_cache.prefix)rspq_block_free(hud_cache.prefix);
    if(hud_cache.suffix)rspq_block_free(hud_cache.suffix);
    for(int i=0;i<2;++i){
        if(hud_cache.stance[i])rspq_block_free(hud_cache.stance[i]);
        if(hud_cache.result[i])rspq_block_free(hud_cache.result[i]);
    }
    for(int i=0;i<HUD_PROMPT_COUNT;++i)if(hud_cache.prompt[i])rspq_block_free(hud_cache.prompt[i]);
    memset(&hud_cache,0,sizeof hud_cache);
}

static void hud_cache_prepare(const DlGame *g){
    if(hud_cache.level==g->level)return;
    assertf(!hud_cache.level,"Release HUD cache before replacing a level");
    static const char *prompts[HUD_PROMPT_COUNT]={
        "Find the switch, open the gate, take the relic.",
        "Gate open. Find the gold relic and press A.",
        "A: operate the gate switch","A: take the relic",
        "Spotted! Break sight and retreat into shadow."};
    char title[97];int end=0;
    for(int i=0;i<48&&g->level->title[i];++i){
        char ch=g->level->title[i];if(ch=='^'||ch=='$')title[end++]=ch;
        title[end++]=ch=='\n'||ch=='\r'||ch=='\t'?' ':ch;
    }
    title[end]='\0';
    rspq_block_begin();
    rdpq_text_print(NULL,1,9,12,"^01D A R K L A N T E R N  6 4");
    rdpq_text_print(NULL,1,9,22,title);
    rdpq_text_print(NULL,1,9,204,"LIGHT");rdpq_text_print(NULL,1,138,204,"ALERT");
    hud_cache.prefix=rspq_block_end();
    rspq_block_begin();
    rdpq_text_print(NULL,1,9,226,"^02Stick: look  C: move/strafe  Z: crouch");
    rdpq_text_print(NULL,1,9,236,"^02A: use B: noise L: jump R: reset START: debug");
    hud_cache.suffix=rspq_block_end();
    for(int i=0;i<2;++i){
        rspq_block_begin();rdpq_text_print(NULL,1,263,204,i?"^01CROUCH":"STAND");
        hud_cache.stance[i]=rspq_block_end();
        rspq_block_begin();
        rdpq_text_print(NULL,1,66,99,i?"^01CAUGHT BY THE WATCH":"^01RELIC ACQUIRED");
        rdpq_text_print(NULL,1,54,114,i?"Use shadow, quiet steps and distractions.":"A quiet theft. The first heist is complete.");
        rdpq_text_print(NULL,1,107,130,"^02R: try again");
        hud_cache.result[i]=rspq_block_end();
    }
    for(int i=0;i<HUD_PROMPT_COUNT;++i){
        rspq_block_begin();rdpq_text_print(NULL,1,9,215,prompts[i]);
        hud_cache.prompt[i]=rspq_block_end();
    }
    for(int stance=0;stance<2;++stance)for(int prompt=0;prompt<HUD_PROMPT_COUNT;++prompt){
        rspq_block_begin();
        rspq_block_run(hud_cache.prefix);rspq_block_run(hud_cache.stance[stance]);
        rspq_block_run(hud_cache.prompt[prompt]);rspq_block_run(hud_cache.suffix);
        hud_cache.composite[stance][prompt]=rspq_block_end();
    }
    hud_cache.level=g->level;
}

static void hud_cache_draw(const DlGame *g,HudPrompt prompt){
    assertf(hud_cache.level==g->level,"HUD cache does not match level");
    rspq_block_run(hud_cache.composite[g->crouched?1:0][prompt]);
}
static void hud_cache_result(bool caught){rspq_block_run(hud_cache.result[caught?1:0]);}
