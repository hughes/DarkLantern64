#include "lighting_policy.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

static void path(DlLevel *level,int model,DlLightingSettings s,DlLightingMode mode,DlLightingReason reason,
    DlLightingMode change,DlLightingReason change_reason){
    DlLightingDecision d=dl_lighting_decision(level,model,s);
    assert(d.mode==mode&&d.reason==reason&&d.change_mode==change&&d.change_reason==change_reason);
}
int main(void){
    static const uint8_t rgba[24]={0};
    DlMesh meshes[2]={{.vertex_count=3,.index_count=3},{.vertex_count=3,.index_count=3,
        .animation=(const struct DlAnimationAsset*)1}};
    DlModelInstance models[6]={
        {.role=DL_MODEL_STATIC,.baked_colors={rgba,rgba}},
        {.role=DL_MODEL_DOOR,.baked_colors={rgba,rgba}},
        {.role=DL_MODEL_GUARD},{.role=DL_MODEL_OBJECTIVE},
        {.role=DL_MODEL_STATIC,.mesh=1},{.role=DL_MODEL_STATIC}};
    DlObjectDef objects[3]={{.kind=DL_OBJECT_DOOR},{.kind=DL_OBJECT_CONTROL},{.kind=DL_OBJECT_DOOR}};
    DlLevel level={.environment={.enabled=true},.meshes=meshes,.mesh_count=2,.models=models,.model_count=6,
        .objects=objects,.object_count=2};
    DlLightingSettings s={.bake_enabled=true,.independent_doors=dl_lighting_independent_doors(&level)};
    assert(s.independent_doors==1);
    path(&level,0,s,DL_LIGHT_INLINE_TWO_STATE,DL_LIGHT_INLINE_PAYLOAD,DL_LIGHT_INLINE_TWO_STATE,DL_LIGHT_INLINE_PAYLOAD);
    path(&level,1,s,DL_LIGHT_INLINE_TWO_STATE,DL_LIGHT_INLINE_PAYLOAD,DL_LIGHT_INLINE_TWO_STATE,DL_LIGHT_INLINE_PAYLOAD);
    path(&level,2,s,DL_LIGHT_ACTOR_BODY_PROBES,DL_LIGHT_GUARD,DL_LIGHT_ACTOR_BODY_PROBES,DL_LIGHT_GUARD);
    path(&level,3,s,DL_LIGHT_OBJECTIVE_PROBE,DL_LIGHT_OBJECTIVE,DL_LIGHT_OBJECTIVE_PROBE,DL_LIGHT_OBJECTIVE);
    path(&level,4,s,DL_LIGHT_LIVE_NIGHT,DL_LIGHT_ANIMATED_MESH,DL_LIGHT_LIVE_NIGHT,DL_LIGHT_ANIMATED_MESH);
    path(&level,5,s,DL_LIGHT_LIVE_NIGHT,DL_LIGHT_NIGHT_WITHOUT_BAKE,DL_LIGHT_RUNTIME_TWO_STATE,DL_LIGHT_CACHED_SINGLE_DOOR_STATES);
    DlLightingPolicyReport report;dl_lighting_policy_report(&level,s,false,&report);
    assert(report.models==6&&report.triangles==6&&report.case_count==5);
    assert(report.cases[DL_LIGHT_INLINE_TWO_STATE][DL_LIGHT_INLINE_PAYLOAD].models==2);
    level.object_count=3;s.independent_doors=dl_lighting_independent_doors(&level);assert(s.independent_doors==2);
    path(&level,0,s,DL_LIGHT_LIVE_NIGHT,DL_LIGHT_INDEPENDENT_DOORS,DL_LIGHT_LIVE_NIGHT,DL_LIGHT_INDEPENDENT_DOORS);
    dl_lighting_policy_report(&level,s,false,&report);
    assert(report.cases[DL_LIGHT_LIVE_NIGHT][DL_LIGHT_INDEPENDENT_DOORS].models==3);
    assert(!dl_lighting_inline_eligible(&level,0,true,2));
    s.standalone_loaded=true;
    path(&level,0,s,DL_LIGHT_STANDALONE_BAKED,DL_LIGHT_STANDALONE_ASSET,DL_LIGHT_LIVE_NIGHT,DL_LIGHT_INDEPENDENT_DOORS);
    dl_lighting_policy_report(&level,s,true,&report);
    assert(report.cases[DL_LIGHT_LIVE_NIGHT][DL_LIGHT_INDEPENDENT_DOORS].models==3);
    s.standalone_loaded=false;s.bake_enabled=false;
    path(&level,0,s,DL_LIGHT_LIVE_NIGHT,DL_LIGHT_DISABLED_BUILD,DL_LIGHT_LIVE_NIGHT,DL_LIGHT_INDEPENDENT_DOORS);
    s.bake_enabled=true;s.independent_doors=1;s.standalone_rejected=true;
    path(&level,5,s,DL_LIGHT_LIVE_NIGHT,DL_LIGHT_STANDALONE_REJECTED,DL_LIGHT_RUNTIME_TWO_STATE,DL_LIGHT_CACHED_SINGLE_DOOR_STATES);
    models[5].baked_colors[0]=rgba;
    path(&level,5,s,DL_LIGHT_LIVE_NIGHT,DL_LIGHT_INCOMPLETE_INLINE_PAYLOAD,DL_LIGHT_RUNTIME_TWO_STATE,DL_LIGHT_CACHED_SINGLE_DOOR_STATES);
    level.environment.enabled=false;
    path(&level,0,s,DL_LIGHT_LEGACY_SCALAR,DL_LIGHT_LEGACY_ENVIRONMENT,DL_LIGHT_LEGACY_SCALAR,DL_LIGHT_LEGACY_ENVIRONMENT);
    path(&level,2,s,DL_LIGHT_LEGACY_ACTOR_PROBES,DL_LIGHT_GUARD,DL_LIGHT_LEGACY_ACTOR_PROBES,DL_LIGHT_GUARD);
    path(&level,3,s,DL_LIGHT_LEGACY_OBJECTIVE_EMISSIVE,DL_LIGHT_OBJECTIVE,DL_LIGHT_LEGACY_OBJECTIVE_EMISSIVE,DL_LIGHT_OBJECTIVE);
    assert(!dl_lighting_static_eligible(&level,0));assert(dl_lighting_decision(NULL,0,s).mode==DL_LIGHT_INVALID);
    assert(dl_lighting_decision(&level,-1,s).mode==DL_LIGHT_INVALID);
    models[0].mesh=99;assert(dl_lighting_decision(&level,0,s).reason==DL_LIGHT_INVALID_MODEL);models[0].mesh=0;
    char row[512],small[512];
    DlLightingPolicyIdentity id={.report=UINT64_MAX,.build="build",.source="source",.package="none",.standalone="unconfigured",.doors=2};
    size_t n=dl_lighting_policy_header_text(row,sizeof row,&id,&report);
    assert(n>0&&row[n]=='\0'&&row[n-1]=='\n');assert(strstr(row,"report=18446744073709551615"));
    for(size_t capacity=0;capacity<=n;++capacity)assert(dl_lighting_policy_header_text(small,capacity,&id,&report)==0);
    assert(dl_lighting_policy_header_text(small,n+1,&id,&report)==n&&memcmp(small,row,n+1)==0);
    static const char hash[]="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
    id.build=id.source=id.package=hash;id.changed=true;id.standalone="checksum";
    id.doors=id.revision=id.published=id.door_revision=UINT32_MAX;report.models=128;report.case_count=13;
    n=dl_lighting_policy_header_text(row,sizeof row-4,&id,&report);assert(n>0&&n<sizeof row-4);
    assert(strstr(row,"scope=published_model_policy"));
    n=dl_lighting_policy_case_text(row,sizeof row,UINT64_MAX,DL_LIGHT_LIVE_NIGHT,DL_LIGHT_INDEPENDENT_DOORS,(DlLightingCount){UINT32_MAX,UINT32_MAX});
    assert(n&&strstr(row,"reason=independent_doors")&&strstr(row,"triangles=4294967295"));
    for(size_t capacity=0;capacity<=n;++capacity)assert(dl_lighting_policy_case_text(small,capacity,UINT64_MAX,DL_LIGHT_LIVE_NIGHT,DL_LIGHT_INDEPENDENT_DOORS,(DlLightingCount){UINT32_MAX,UINT32_MAX})==0);
    n=dl_lighting_policy_cost_text(row,sizeof row,UINT64_MAX,UINT32_MAX,46875000);assert(n&&strstr(row,"scope=policy_rows_and_transport"));
    for(size_t capacity=0;capacity<=n;++capacity)assert(dl_lighting_policy_cost_text(small,capacity,UINT64_MAX,UINT32_MAX,46875000)==0);
    puts("PASS authoritative lighting decisions, two-door fallback, independent changes, aggregate counts and bounded record encoding");return 0;
}
