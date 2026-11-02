package protocol

import (
 "context"
 "encoding/json"
 "os"
 "path/filepath"
 "testing"
)

func TestEaselPPURef11JSONWireContract(t *testing.T) {
 path:=filepath.Join("..","..","..","..","..","..","integrations","beeftv","ppu-h3-ref11-json","manifest.json")
 data,err:=os.ReadFile(path);if err!=nil {t.Fatal(err)}
 adapters,err:=LoadInstalledProviders(data,nil);if err!=nil||len(adapters)!=1 {t.Fatalf("load: %v %d",err,len(adapters))}
 adapter:=adapters[0]
 request:=GenerationRequest{Model:"minimax-h3-ref2va-ref11-8step",Prompt:"fixture only",Duration:6,AspectRatio:"9:16",Images:[]MediaReference{{DataURL:"data:image/png;base64,fixture",Kind:"image",Role:"reference_image"}},Extra:map[string]any{"idempotencyKey":"stable-ref11-fixture"}}
 spec,err:=adapter.BuildCreate(context.Background(),RequestContext{BaseURL:"http://127.0.0.1:8881",Request:request});if err!=nil {t.Fatal(err)}
 encoded,_:=json.Marshal(spec.Body);var body map[string]any;json.Unmarshal(encoded,&body)
 if spec.ContentType!="application/json"||spec.Path!="/v1/videos"||body["seconds"]!=float64(6)||body["size"]!="9:16"||body["request_id"]!="stable-ref11-fixture" {t.Fatalf("wire: %s",encoded)}
 images,ok:=body["images"].([]any);if !ok||len(images)!=1||images[0].(map[string]any)["data_url"]!="data:image/png;base64,fixture" {t.Fatalf("image reference: %s",encoded)}
 request.Images=nil
 if _,err=adapter.BuildCreate(context.Background(),RequestContext{Request:request});err==nil {t.Fatal("Ref11 must reject reference-free generation")}
}
