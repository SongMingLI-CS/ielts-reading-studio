// 知识点手册：筛选条改动即刻提交（视图是 GET 表单，所有状态都在地址里，可收藏可分享）。
document.addEventListener("DOMContentLoaded", () => {
  const form = document.querySelector("form.kn-filter[data-autosubmit]");
  if (!form) return;
  const status = document.querySelector("#kn-filter-status");
  const submit = () => {
    if (form.dataset.submitting === "true") return;
    form.dataset.submitting = "true";
    form.setAttribute("aria-busy", "true");
    const button = form.querySelector('button[type="submit"]');
    if (button) { button.disabled = true; button.textContent = "正在更新…"; }
    if (status) status.textContent = "正在应用筛选条件…";
    form.submit();
  };
  form.querySelectorAll("select").forEach((select) => {
    select.addEventListener("change", submit);
  });
  form.querySelectorAll('input[type="checkbox"]').forEach((box) => {
    box.addEventListener("change", submit);
  });
  const search = form.querySelector('input[name="q"]');
  if (search) {
    search.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        submit();
      }
    });
  }
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    submit();
  });

  const active = document.querySelector("#kn-active-filters");
  const defaults = new Set(["", "all", "alpha"]);
  const labels = [];
  form.querySelectorAll("select, input[type='search'], input[type='checkbox']").forEach((control) => {
    if (control.type === "checkbox") {
      if (control.checked) labels.push(control.closest("label").textContent.trim());
      return;
    }
    if (!defaults.has(control.value)) {
      const name = control.closest("label").querySelector("span").textContent.trim();
      const value = control.tagName === "SELECT" ? control.selectedOptions[0].textContent.trim() : control.value;
      labels.push(`${name}：${value}`);
    }
  });
  if (active && labels.length) {
    active.hidden = false;
    const title = document.createElement("strong");
    title.textContent = "当前筛选";
    active.append(title, ...labels.map((label) => {
      const chip = document.createElement("span");
      chip.textContent = label;
      return chip;
    }));
  }

  if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    document.querySelectorAll(".kn-card").forEach((card, index) => {
      card.classList.add("kn-card-enter");
      card.style.setProperty("--kn-enter-delay", `${Math.min(index, 10) * 35}ms`);
    });
  }
  // 展开全部解析，方便老师/自学者一次读完
  const openAll = document.querySelector("[data-open-details]");
  if (openAll) {
    openAll.addEventListener("click", () => {
      document.querySelectorAll("details.kn-detail").forEach((node) => {
        node.open = true;
      });
    });
  }
});
