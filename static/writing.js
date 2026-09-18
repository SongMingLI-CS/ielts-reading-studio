(() => {
  const form = document.querySelector("#writing-form");
  if (!form) return;

  const essay = document.querySelector("#writing-essay");
  const count = document.querySelector("#essay-count");
  const button = document.querySelector("#evaluate-button");
  const status = document.querySelector("#writing-status");
  const result = document.querySelector("#writing-result");

  const wordCount = (text) => (text.trim().match(/[A-Za-z]+(?:['’-][A-Za-z]+)*/g) || []).length;
  const updateCount = () => { count.textContent = `${wordCount(essay.value)} 词`; };
  essay.addEventListener("input", updateCount);

  const setList = (selector, values) => {
    const list = document.querySelector(selector);
    list.replaceChildren(...values.map((value) => {
      const item = document.createElement("li");
      item.textContent = value;
      return item;
    }));
  };

  const criterionCard = (label, criterion) => {
    const card = document.createElement("article");
    card.className = "panel criterion-card";
    const heading = document.createElement("div");
    const title = document.createElement("h3");
    const band = document.createElement("strong");
    const feedback = document.createElement("p");
    title.textContent = label;
    band.textContent = criterion.band.toFixed(1);
    feedback.textContent = criterion.feedback;
    heading.append(title, band);
    card.append(heading, feedback);
    return card;
  };

  const showResult = (data) => {
    document.querySelector("#overall-band").textContent = data.overall_band.toFixed(1);
    const fulfilment = data.task_achievement
      ? ["Task Achievement", data.task_achievement]
      : ["Task Response", data.task_response];
    document.querySelector("#criteria-grid").replaceChildren(
      criterionCard(...fulfilment),
      criterionCard("Coherence & Cohesion", data.coherence_and_cohesion),
      criterionCard("Lexical Resource", data.lexical_resource),
      criterionCard("Grammar", data.grammatical_range_and_accuracy),
    );
    document.querySelector("#general-feedback").textContent = data.general_feedback;
    setList("#strengths", data.strengths);
    setList("#improvements", data.improvements);
    const sampleCard = document.querySelector("#sample-card");
    const sample = document.querySelector("#sample-answer");
    sample.replaceChildren();
    if (data.sample_answer) {
      data.sample_answer.split(/\n{2,}/).forEach((paragraph) => {
        const node = document.createElement("p");
        node.textContent = paragraph;
        sample.append(node);
      });
      sampleCard.hidden = false;
    } else {
      sampleCard.hidden = true;
    }
    result.hidden = false;
    result.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const errorMessage = (response, body) => {
    if (response.status === 422) return "请检查题目和作文是否完整，并满足最小长度。";
    const detail = body && body.detail;
    if (detail && typeof detail.message === "string") return `评估服务暂时不可用：${detail.message}`;
    return "评估失败，请稍后重试或到设置页检查模型连接。";
  };

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    button.disabled = true;
    button.textContent = "正在评估…";
    status.className = "is-working";
    status.textContent = "模型正在阅读并评分，请保持页面打开。";
    try {
      const response = await fetch("/api/writing/evaluate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          task_type: new FormData(form).get("task_type"),
          question: document.querySelector("#writing-question").value,
          essay: essay.value,
          title: document.querySelector("#writing-title").value || null,
          include_sample_answer: document.querySelector("#include-sample").checked,
        }),
      });
      let body = null;
      try { body = await response.json(); } catch (_) { /* handled below */ }
      if (!response.ok) throw new Error(errorMessage(response, body));
      showResult(body);
      status.className = "is-success";
      status.textContent = "评估完成，结果已保存。";
    } catch (error) {
      status.className = "is-error";
      status.textContent = error instanceof Error ? error.message : "评估失败，请稍后重试。";
    } finally {
      button.disabled = false;
      button.textContent = "重新评估";
    }
  });
})();