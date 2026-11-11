(() => {
  "use strict";

  const menuButton = document.getElementById("docs-menu-button");
  const sidebar = document.getElementById("docs-sidebar");
  const copyButtons = document.querySelectorAll("[data-copy-target]");
  const navLinks = [...document.querySelectorAll(".docs-sidebar nav a")];

  const closeMenu = () => {
    sidebar?.classList.remove("open");
    menuButton?.setAttribute("aria-expanded", "false");
    menuButton?.setAttribute("aria-label", "打开文档导航");
  };

  menuButton?.addEventListener("click", () => {
    const open = sidebar?.classList.toggle("open") || false;
    menuButton.setAttribute("aria-expanded", String(open));
    menuButton.setAttribute("aria-label", open ? "关闭文档导航" : "打开文档导航");
  });

  navLinks.forEach((link) => link.addEventListener("click", closeMenu));

  copyButtons.forEach((button) => {
    button.addEventListener("click", async () => {
      const target = document.getElementById(button.dataset.copyTarget || "");
      if (!target) return;
      const original = button.textContent;
      button.textContent = "已复制";
      window.setTimeout(() => { button.textContent = original; }, 1600);
      try {
        await navigator.clipboard.writeText(target.textContent);
      } catch {
        const textarea = document.createElement("textarea");
        textarea.value = target.textContent;
        textarea.style.position = "fixed";
        textarea.style.opacity = "0";
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand("copy");
        textarea.remove();
      }
    });
  });

  const sections = [...document.querySelectorAll(".docs-section[id]")];
  if ("IntersectionObserver" in window && sections.length) {
    const observer = new IntersectionObserver((entries) => {
      const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
      if (!visible) return;
      navLinks.forEach((link) => {
        link.classList.toggle("active", link.getAttribute("href") === "#" + visible.target.id);
      });
    }, { rootMargin: "-18% 0px -65% 0px", threshold: [0, 0.2, 0.6] });
    sections.forEach((section) => observer.observe(section));
  }
})();
