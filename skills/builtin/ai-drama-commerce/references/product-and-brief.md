# 商品、简报和数据来源

事实卡建议包含：

```json
{
  "product_id": "来源中的商品编号",
  "sku": "本片采用规格",
  "name": "商品名",
  "sources": [{"id":"source-1","url":"官方商品页","checked_at":"ISO时间"}],
  "claims": [{"id":"claim-1","text":"具体卖点","status":"verified|brand_claim|unverified","source_ids":["source-1"],"conditions":"适用条件"}],
  "assets": [{"path":"assets/实拍或参考图","sha256":"实际摘要","source_id":"source-1","usage":"真实许可或使用范围"}],
  "offer": {"price":null,"checked_at":null,"expires_at":null},
  "limitations": []
}
```

网页公开可读不等于已取得商业投放素材许可。实际发布需要核对相应授权。私下创意样片应记录真实素材来源和使用边界；不冒充品牌官方广告。

价格、库存和优惠是时效信息。没有实时核实就不写确定的价格／库存／倒计时。页面未选地区时，不把页面状态解释为全国库存。

简报写：目标、受众、购买动机、主推卖点、证据、故事结构、角色、视觉和声音、用户已确认规格、资源范围、预算及交付。没有商品事实时允许继续创意草稿，但待补字段不能变成成片事实。

逐镜建议字段：`id / duration / intent / framing / action / speakers / dialogue / product_id / claim_ids / references / prompt / continuity_in / continuity_out / task_id / selected_path / qa`。只保留本次有用的字段，不为凑表而填未测数据。

经营数据分别保留平台、账号、内容 ID、商品／SKU、观察时间窗、归因口径、退款结算窗口和原始导出。点击率与成交率必须标明分母；样本不足时报告限制。可对同一商品做单变量开头测试，共用已验收中段；不能同时改多个因素后声称某一因素造成增长。
