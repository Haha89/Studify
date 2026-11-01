// Dash leaves a button's spinner running when a request can't reach the server, which
// looks like a hang. Say what happened instead: a page reload is the only way out.
(function () {
    const realFetch = window.fetch;
    window.fetch = function (url, options) {
        return realFetch(url, options).catch(function (error) {
            if (String(url).includes("_dash-update-component") && !document.getElementById("server-down")) {
                const banner = document.createElement("div");
                banner.id = "server-down";
                banner.textContent =
                    "Le serveur Studify ne répond plus. Relance-le avec « uv run studify-web », puis recharge cette page.";
                document.body.prepend(banner);
            }
            throw error;
        });
    };
})();
