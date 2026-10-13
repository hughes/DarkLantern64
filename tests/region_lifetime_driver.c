/* Host trace driver for the TLA PreCheck interpreter reference.
 * This tests C transition conformance; it is not a C formal-equivalence proof. */
#include "world_stream.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

static uint8_t package[320], buffer[64];
static DlMission mission;
static DlRegionSlot slot;
static DlWorldStream stream;
static DlRegionHandle handle;
static int read_mode, corrupt_read, owner_generation;
static uint64_t rsp_issued, rdp_issued;

static void put16(uint8_t *p, uint16_t value) { p[0]=(uint8_t)(value>>8);p[1]=(uint8_t)value; }
static void put32(uint8_t *p, uint32_t value) {
    p[0]=(uint8_t)(value>>24);p[1]=(uint8_t)(value>>16);
    p[2]=(uint8_t)(value>>8);p[3]=(uint8_t)value;
}
static size_t read_bytes(void *context, uint32_t offset, void *destination, size_t wanted) {
    (void)context;
    if (read_mode==1) return 0;
    if (read_mode==2) return SIZE_MAX;
    assert(offset<=sizeof package && wanted<=sizeof package-offset);
    memcpy(destination,package+offset,wanted);
    if (corrupt_read && offset==192 && wanted) ((uint8_t *)destination)[0]^=1;
    return wanted;
}
static void reset(int corrupt) {
    memset(package,0,sizeof package);
    memcpy(package,"DLM1",4);put16(package+4,1);put16(package+6,64);
    put32(package+8,sizeof package);put32(package+12,2);
    put32(package+20,192);put32(package+24,1);
    for (unsigned i=0;i<2;++i) {
        uint8_t *chunk=package+192+64*i;
        memcpy(chunk,"DLR1",4);put16(chunk+4,1);put16(chunk+6,32);
        put32(chunk+8,64);put32(chunk+12,1);
        put16(chunk+32,DL_MISSION_SECTION_TRIANGLES);
        put32(chunk+36,64);put32(chunk+48,DL_MISSION_TRIANGLE_BYTES);
        uint8_t *region=package+64+64*i;
        put32(region,i+1);put32(region+4,192+64*i);
        put32(region+8,64);put32(region+12,64);
        put32(region+16,dl_mission_crc32_update(UINT32_MAX,chunk,64)^UINT32_MAX);
        put32(region+44,0x3f800000u);put32(region+48,0x3f800000u);put32(region+52,0x3f800000u);
    }
    put32(package+28,dl_mission_crc32_update(UINT32_MAX,package,192)^UINT32_MAX);
    DlMissionStatus status=dl_mission_open(package,192,sizeof package,&mission);
    if (status!=DL_MISSION_OK) fprintf(stderr,"fixture: %s\n",dl_mission_status_name(status));
    assert(status==DL_MISSION_OK);
    slot=(DlRegionSlot){.buffer=buffer,.capacity=sizeof buffer};
    assert(dl_world_stream_init(&stream,&mission,&slot,1,read_bytes,NULL)==DL_STREAM_OK);
    handle=(DlRegionHandle){0};read_mode=0;corrupt_read=corrupt;owner_generation=0;
    rsp_issued=rdp_issued=0;
}
static void action(const char *name) {
    const DlMissionChunk *chunk=NULL;
    if (!strcmp(name,"reserve")) assert(dl_world_stream_request(&stream,1,&handle)==DL_STREAM_OK);
    else if (!strcmp(name,"startRead")) { read_mode=1;assert(dl_world_stream_pump(&stream,64)==0); }
    else if (!strcmp(name,"readComplete")) { read_mode=0;assert(dl_world_stream_pump(&stream,64)==64);corrupt_read=0; }
    else if (!strcmp(name,"publish") || !strcmp(name,"abortValidate")) {
        assert(dl_world_stream_pump(&stream,1)==0);
    } else if (!strcmp(name,"abortRead")) { read_mode=2;assert(dl_world_stream_pump(&stream,64)==0); }
    else if (!strcmp(name,"cancelUnpublished")) assert(dl_world_stream_cancel(&stream,handle)==DL_STREAM_OK);
    else if (!strcmp(name,"borrow")) {
        assert(dl_world_stream_borrow(&stream,handle,&chunk)==DL_STREAM_OK);
        assert(chunk && chunk->data==buffer);owner_generation=(int)(handle.generation%2);
    } else if (!strcmp(name,"release")) assert(dl_world_stream_release(&stream,handle)==DL_STREAM_OK);
    else if (!strcmp(name,"submitRsp")) assert(dl_world_stream_submit(&stream,handle,++rsp_issued,0)==DL_STREAM_OK);
    else if (!strcmp(name,"submitRdp")) assert(dl_world_stream_submit(&stream,handle,0,++rdp_issued)==DL_STREAM_OK);
    else if (!strcmp(name,"completeRsp")) assert(dl_world_stream_complete(&stream,rsp_issued,stream.completed_rdp)==DL_STREAM_OK);
    else if (!strcmp(name,"completeRdp")) assert(dl_world_stream_complete(&stream,stream.completed_rsp,rdp_issued)==DL_STREAM_OK);
    else if (!strcmp(name,"retire")) assert(dl_world_stream_retire(&stream,handle)==DL_STREAM_OK);
    else assert(!"Unknown action");
}
static void output(void) {
    static const char *const phases[]={"free","requested","reading","validating","resident","retiring","failed"};
    assert((unsigned)slot.state<sizeof phases/sizeof phases[0]);
    /* Prove that a rejected borrow does not create ownership in prepublication,
     * failed, free or retiring states. */
    if (slot.state!=DL_STREAM_RESIDENT) {
        const DlMissionChunk *chunk=NULL;
        uint32_t pins=slot.cpu_pins;
        assert(dl_world_stream_borrow(&stream,handle,&chunk)!=DL_STREAM_OK);
        assert(slot.cpu_pins==pins && !chunk);
    }
    printf("{\"phase\":\"%s\",\"generation\":%s,\"ownerGeneration\":%s,\"cpu\":%s,\"rsp\":%s,\"rdp\":%s}\n",
        phases[slot.state],slot.generation%2?"true":"false",owner_generation?"true":"false",
        slot.cpu_pins?"true":"false",slot.last_rsp>stream.completed_rsp?"true":"false",
        slot.last_rdp>stream.completed_rdp?"true":"false");
    fflush(stdout);
}
int main(void) {
    char line[64];
    while (fgets(line,sizeof line,stdin)) {
        line[strcspn(line,"\r\n")]=0;
        if (!strncmp(line,"reset ",6)) reset(line[6]=='1');
        else if (!strcmp(line,"corruptNextRead")) corrupt_read=1;
        else { action(line); output(); }
    }
    return 0;
}
