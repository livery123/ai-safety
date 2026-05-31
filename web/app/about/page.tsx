export default function AboutPage() {
  return (
    <div className="mx-auto max-w-3xl space-y-6 rounded-2xl border border-slate-200 bg-white p-8 shadow-card">
      <h1 className="text-3xl font-bold text-slate-900">关于平台</h1>
      <p className="leading-relaxed text-slate-600">
        全球 AI 治理监测平台自动感知 AI 安全与监管动态，基于三元风险模型对政策、会议与文献进行结构化分类，
        为研究者与公众提供可浏览、可检索的情报门户。
      </p>
      <p className="leading-relaxed text-slate-600">
        本门户与内部 Streamlit 管理后台并行运行：公众在此浏览情报，运维人员在后台执行信源同步与系统管理。
      </p>
    </div>
  );
}
