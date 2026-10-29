/**
 * 量一下流式期间 Markdown 重渲染的开销。
 * 场景：1400 字答案，每秒 64 个 delta，每个 delta 触发一次全量重渲染。
 */
import MarkdownIt from 'markdown-it'

const md = new MarkdownIt()
const text = `我跟你说，你这个情况啊，就是太阳伤寒，一点都不复杂。怕冷、没有汗、脖子后面发紧，这三个凑在一起，寒邪把表给束住了，对不对？

你看啊，正常人皮肤表面有一层水气，汗要出来，毛孔得开着。寒一束，毛孔闭上，汗出不去，人就抖——这就是恶寒的来路。**关键**：有汗是中风，无汗是伤寒，这一条不能错。

**辨证：太阳伤寒证（寒邪束表、卫阳被遏·麻黄汤证）**

| 主证 | 表现 | 说明 |
|---|---|---|
| 恶寒 | 怕冷明显，加衣不减 | 寒束肌表 |
| 无汗 | 一点汗都没有 | 与中风鉴别的关键 |
| 身痛 | 全身骨节酸痛 | 寒主收引 |
| 脉浮紧 | 轻取即得、绷紧 | 表实之象 |
| 喘 | 部分人有 | 肺气不宣 |

我问你，为什么麻黄用量一定要大于桂枝？你想想看，麻黄开毛孔，是主力；桂枝解肌，是副手。主次一颠倒，汗出不来，病就拖住了。

> 📜 **伤寒论·第35条**「太阳病，头痛发热，身疼腰痛，骨节疼痛，恶风无汗而喘者，麻黄汤主之。」
> **原方**：麻黄(三两) 桂枝(二两) 杏仁(七十个) 炙甘草(一两)
> **倪师临床**：麻黄(3钱) 桂枝(2钱) 杏仁(3钱) 炙甘草(2钱)

记住了，太阳伤寒就一条路——开表发汗，就这么简单。`

// 模拟流式：内容逐步变长
const steps = 200
const t0 = performance.now()
for (let i = 1; i <= steps; i++) {
  const partial = text.slice(0, Math.floor((text.length * i) / steps))
  md.render(partial)
}
const t1 = performance.now()

const per = (t1 - t0) / steps
console.log(`全文渲染 ${steps} 次，平均 ${per.toFixed(3)} ms/次`)
console.log(`按 64 次/秒 计算，每秒耗时 ${(per * 64).toFixed(1)} ms（占一帧的 ${((per * 64) / 16.67 * 100).toFixed(0)}%）`)

// 纯文本方案对比
const t2 = performance.now()
for (let i = 1; i <= steps; i++) {
  const partial = text.slice(0, Math.floor((text.length * i) / steps))
  partial.replace(/\*\*/g, '').replace(/\n/g, '<br>')
}
const t3 = performance.now()
console.log(`纯文本方案平均 ${((t3 - t2) / steps).toFixed(4)} ms/次`)
