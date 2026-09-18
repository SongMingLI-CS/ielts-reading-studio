// 知识点手册：筛选条改动即刻提交（视图是 GET 表单，所有状态都在地址里，可收藏可分享）。
document.addEventListener("DOMContentLoaded", () => {
  const form = document.querySelector("form.kn-filter[data-autosubmit]");
  if (!form) return;
  form.querySelectorAll("select").forEach((select) => {
    select.addEventListener("change", () => form.submit());
  });
  form.querySelectorAll('input[type="checkbox"]').forEach((box) => {
    box.addEventListener("change", () => form.submit());
  });
  const search = form.querySelector('input[name="q"]');
  if (search) {
    search.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        form.submit();
      }
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
