document.addEventListener("DOMContentLoaded", () => {
    const csrfToken = document.body.dataset.csrfToken || "";

    document.querySelectorAll(".flash").forEach((flash) => {
        flash.addEventListener("click", () => flash.remove());
        setTimeout(() => {
            flash.style.opacity = "0";
            setTimeout(() => flash.remove(), 280);
        }, 3600);
    });

    document.querySelectorAll(".nav-dropdown").forEach((dropdown) => {
        const button = dropdown.querySelector(".nav-user");
        const menu = dropdown.querySelector(".dropdown-menu");
        if (!button || !menu) {
            return;
        }
        button.addEventListener("click", (event) => {
            event.preventDefault();
            menu.classList.toggle("show");
        });
        document.addEventListener("click", (event) => {
            if (!dropdown.contains(event.target)) {
                menu.classList.remove("show");
            }
        });
    });

    document.querySelectorAll("[data-confirm]").forEach((button) => {
        button.addEventListener("click", (event) => {
            if (!window.confirm(button.dataset.confirm)) {
                event.preventDefault();
            }
        });
    });

    document.querySelectorAll(".reply-toggle").forEach((button) => {
        button.addEventListener("click", () => {
            const replyForm = button.closest(".comment-card")?.querySelector(".reply-form");
            if (!replyForm) {
                return;
            }
            replyForm.classList.toggle("hidden");
        });
    });

    const likeButton = document.querySelector(".like-button");
    if (likeButton) {
        likeButton.addEventListener("click", async () => {
            const articleId = likeButton.dataset.articleId;
            const response = await fetch(`/like/${articleId}`, {
                method: "POST",
                headers: {
                    "X-CSRF-Token": csrfToken,
                },
            });
            if (response.redirected) {
                window.location.href = response.url;
                return;
            }
            const data = await response.json();
            if (!response.ok) {
                window.alert("点赞失败，请稍后重试。");
                return;
            }
            likeButton.classList.toggle("active", data.liked);
            likeButton.querySelector(".like-icon").textContent = data.liked ? "♥" : "♡";
            likeButton.querySelector(".like-count").textContent = data.count;
        });
    }

    const previewButton = document.querySelector("#preview-btn");
    const contentField = document.querySelector("#content");
    const previewArea = document.querySelector("#preview-area");
    if (previewButton && contentField && previewArea) {
        previewButton.addEventListener("click", async () => {
            const response = await fetch("/preview-markdown", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRF-Token": csrfToken,
                },
                body: JSON.stringify({
                    content: contentField.value,
                }),
            });
            const data = await response.json();
            if (!response.ok) {
                previewArea.innerHTML = "<p>预览失败，请刷新页面后重试。</p>";
                return;
            }
            previewArea.innerHTML = data.html || "<p>当前内容为空。</p>";
        });
    }

    const keyword = document.querySelector(".search-highlight-term")?.dataset.term;
    if (keyword) {
        const escaped = keyword.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        const pattern = new RegExp(`(${escaped})`, "gi");
        document.querySelectorAll(".search-result-title, .search-result-excerpt").forEach((element) => {
            element.innerHTML = element.innerHTML.replace(pattern, "<mark>$1</mark>");
        });
    }
});
