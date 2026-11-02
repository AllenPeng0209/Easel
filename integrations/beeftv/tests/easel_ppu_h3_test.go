package protocol

import (
    "context"
    "encoding/json"
    "os"
    "path/filepath"
    "testing"
)

func TestEaselPPUH3JSONWireContract(t *testing.T) {
    data, err := os.ReadFile(filepath.Join("..","..","..","..","..","..","integrations","beeftv","ppu-h3-json","manifest.json"))
    if err != nil { t.Fatal(err) }
    adapters, err := LoadInstalledProviders(data, nil)
    if err != nil || len(adapters) != 1 { t.Fatalf("load: %v %d", err, len(adapters)) }
    adapter := adapters[0]
    request := GenerationRequest{Model:"minimax-h3-dense8", Prompt:"fixture only", Duration:6, AspectRatio:"9:16", Extra:map[string]any{"idempotencyKey":"fixture-stable-task-id"}, ProviderOptions:map[string]map[string]any{"easel-ppu-h3-json":{"seed":2026110201}}}
    spec, err := adapter.BuildCreate(context.Background(), RequestContext{BaseURL:"http://127.0.0.1:8878", Request:request})
    if err != nil { t.Fatal(err) }
    if spec.Path != "/v1/videos" || spec.ContentType != "application/json" { t.Fatalf("wrong transport: %#v", spec) }
    encoded,_ := json.Marshal(spec.Body); var body map[string]any; json.Unmarshal(encoded,&body)
    if body["seconds"] != float64(6) || body["size"] != "9:16" || body["request_id"] != "fixture-stable-task-id" { t.Fatalf("wrong request: %s",encoded) }
    for _, field := range []string{"fps","generate_audio","duration","aspect_ratio","image"} { if _,ok:=body[field];ok {t.Fatalf("unexpected field %s",field)} }
    request.Images=[]MediaReference{{DataURL:"data:image/png;base64,fixture",Kind:"image",Role:"first_frame"}}
    spec,err=adapter.BuildCreate(context.Background(),RequestContext{Request:request})
    if err!=nil {t.Fatal(err)}
    encoded,_=json.Marshal(spec.Body);json.Unmarshal(encoded,&body)
    if body["image"]!="data:image/png;base64,fixture" {t.Fatalf("image mapping: %s",encoded)}
    poll,err:=adapter.BuildPoll(context.Background(),PollContext{TaskID:"fixture-task"})
    if err!=nil || poll.Path!="/v1/videos/fixture-task" {t.Fatalf("poll: %#v %v",poll,err)}
    result,err:=adapter.ParsePoll(context.Background(),PollContext{TaskID:"fixture-task"},[]byte(`{"id":"fixture-task","status":"completed"}`))
    if err!=nil || result.Status!=StatusSucceeded {t.Fatalf("parse: %#v %v",result,err)}
}
