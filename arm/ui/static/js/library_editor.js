/*
 * Library file editor (C35/C37): Library > Files > one item, and the job page's Files section.
 * Event delegation, so it keeps working after the job page swaps in a preview.
 */
(function () {
    "use strict";

    function showTv(editor) {
        const kind = editor.querySelector(".lib-kind");
        if (!kind) { return; }
        editor.querySelectorAll(".lib-tv").forEach(function (el) {
            el.style.display = kind.value === "series" ? "" : "none";
        });
    }

    function initAll() { document.querySelectorAll(".arm-lib-editor").forEach(showTv); }

    document.addEventListener("change", function (event) {
        if (event.target.classList.contains("lib-kind")) { showTv(event.target.closest(".arm-lib-editor")); }
    });

    document.addEventListener("click", function (event) {
        const searchBtn = event.target.closest(".lib-search");
        if (searchBtn) {
            const editor = searchBtn.closest(".arm-lib-editor");
            const box = editor.querySelector(".lib-results");
            const kind = editor.querySelector(".lib-kind").value;
            box.textContent = "Searching…";
            fetch("/library/files/search?kind=" + kind + "&q=" + encodeURIComponent(editor.querySelector(".lib-query").value))
                .then(function (r) { return r.json(); }).then(function (data) {
                    box.textContent = data.results.length ? "" : (data.error || "Nothing found.");
                    data.results.forEach(function (r) {
                        const b = document.createElement("button");
                        b.type = "button";
                        b.className = "arm-poster-card";
                        const pic = document.createElement(r.poster_url ? "img" : "div");
                        pic.className = "arm-poster-img";
                        if (r.poster_url) { pic.src = r.poster_url; pic.alt = ""; pic.loading = "lazy"; }
                        b.appendChild(pic);
                        const name = document.createElement("span");
                        name.className = "arm-poster-name";
                        name.textContent = r.title;
                        b.appendChild(name);
                        const meta = document.createElement("span");
                        meta.className = "arm-poster-meta";
                        meta.textContent = (r.year || "") + (r.imdb_id ? "" : " · no IMDb id");
                        b.appendChild(meta);
                        b.addEventListener("click", function () {
                            box.querySelectorAll(".arm-poster-card").forEach(function (c) { c.classList.remove("chosen"); });
                            b.classList.add("chosen");
                            editor.querySelector(".lib-title").value = r.title;
                            editor.querySelector(".lib-year").value = r.year;
                            editor.querySelector(".lib-imdb").value = r.imdb_id;
                        });
                        box.appendChild(b);
                    });
                }).catch(function () { box.textContent = "Search failed."; });
            return;
        }
        const confirmBtn = event.target.closest(".arm-lib-editor [data-confirm]");
        if (confirmBtn && !confirm(confirmBtn.dataset.confirm)) { event.preventDefault(); }
    });

    // On the job page, Preview swaps the editor in place instead of leaving the page
    document.addEventListener("submit", function (event) {
        const form = event.target.closest(".arm-lib-form");
        if (!form) { return; }
        const editor = form.closest(".arm-lib-editor");
        const submitter = event.submitter;
        if (!editor.dataset.back || !submitter || submitter.value !== "preview") { return; }
        event.preventDefault();
        const data = new FormData(form);
        data.set("action", "preview");
        data.set("partial", "1");
        submitter.disabled = true;
        fetch(form.action, {method: "POST", body: data, credentials: "same-origin"})
            .then(function (r) { return r.text(); })
            .then(function (html) {
                const holder = document.createElement("div");
                holder.innerHTML = html;
                const fresh = holder.firstElementChild;
                editor.replaceWith(fresh);
                showTv(fresh);
                const preview = fresh.querySelector(".arm-lib-actions");
                if (preview) { preview.scrollIntoView({block: "nearest"}); }
            })
            .catch(function () { submitter.disabled = false; alert("Preview failed."); });
    });

    if (document.readyState === "loading") { document.addEventListener("DOMContentLoaded", initAll); } else { initAll(); }
})();
