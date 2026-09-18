(() => {
  const form = document.querySelector("#writing-form");
  if (!form) return;

  const essay = document.querySelector("#writing-essay");
  const count = document.querySelector("#essay-count");
  const button = document.querySelector("#evaluate-button");
  const status = document.querySelector("#writing-status");
  const result = document.querySelector("#writing-result");
  const loading = document.querySelector("#writing-loading");
  const stage = document.querySelector("#writing-stage");

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

  let stageTimers = [];
  const stopStages = () => {
    stageTimers.forEach((timer) => window.clearTimeout(timer));
    stageTimers = [];
  };
  const startStages = () => {
    const stages = [
      [0, "正在准备评估内容…"],
      [8000, "模型正在阅读作文并检查任务回应…"],
      [25000, "正在整理四项评分标准与改进建议…"],
      [55000, "评估仍在进行，复杂作文可能需要更长时间…"],
    ];
    stopStages();
    stages.forEach(([delay, message]) => {
      stageTimers.push(window.setTimeout(() => { stage.textContent = message; }, delay));
    });
  };

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    button.disabled = true;
    button.textContent = "正在评估…";
    status.className = "is-working";
    status.textContent = "模型正在阅读并评分；你的题目和作文会保留在页面中。";
    loading.hidden = false;
    loading.setAttribute("aria-hidden", "false");
    startStages();
    try {
      const data = await window.AppRequest.requestJson("/api/writing/evaluate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          task_type: new FormData(form).get("task_type"),
          question: document.querySelector("#writing-question").value,
          essay: essay.value,
          title: document.querySelector("#writing-title").value || null,
          include_sample_answer: document.querySelector("#include-sample").checked,
        }),
        timeout: 90000,
      });
      showResult(data);
      status.className = "is-success";
      status.textContent = "评估完成，结果已保存。";
    } catch (error) {
      status.className = "is-error";
      if (error.status === 422) status.textContent = "请检查题目和作文是否完整，并满足最小长度。";
      else status.textContent = `${error.message || "评估失败。"} 作文内容仍在，可直接重试。`;
    } finally {
      stopStages();
      loading.hidden = true;
      loading.setAttribute("aria-hidden", "true");
      button.disabled = false;
      button.textContent = status.className === "is-error" ? "重试评估" : "重新评估";
    }
  });
})();