// The "Modifier le relief" window: paint how high each stud stands, then apply it.
window.studifyReliefEditor = (function () {
    const WORDS = {
        fr: {
            raise: "Monter",
            lower: "Baisser",
            flatten: "Mettre à plat",
            brush: "Pinceau",
            heightsOnly: "Hauteurs seules",
            undo: "Annuler",
            reset: "Tout effacer",
            apply: "Appliquer",
            hint: "Peins sur la mosaïque. Plus c'est rouge, plus c'est haut. Le cadre ne bouge pas, et les zones de moins de 3 tenons de large sont retirées.",
            failed: "Impossible d'enregistrer : recharge la page.",
            saving: "Enregistrement…",
        },
        en: {
            raise: "Raise",
            lower: "Lower",
            flatten: "Flatten",
            brush: "Brush",
            heightsOnly: "Heights only",
            undo: "Undo",
            reset: "Clear all",
            apply: "Apply",
            hint: "Paint on the mosaic. The redder, the higher. The frame stays put, and areas under 3 studs wide are removed.",
            failed: "Couldn't save: reload the page.",
            saving: "Saving…",
        },
    };
    const BRUSHES = [3, 5, 9, 15];
    const UNDO_LIMIT = 30;

    let current = null;
    let request = 0;

    function waitForElement(id) {
        return new Promise(function (resolve, reject) {
            const start = performance.now();
            (function look() {
                const element = document.getElementById(id);
                if (element) {
                    resolve(element);
                } else if (performance.now() - start > 5000) {
                    reject(new Error("no editor stage"));
                } else {
                    requestAnimationFrame(look);
                }
            })();
        });
    }

    function open(stage, url, data, words) {
        const {rows, cols, palette, colors, heights, border, max_height: maxHeight} = data;
        const grid = Int32Array.from(heights);
        const undo = [];
        let tool = "raise";
        let brush = BRUSHES[1];
        let heightsOnly = false;

        const root = document.createElement("div");
        root.className = "relief-editor";
        const bar = document.createElement("div");
        bar.className = "relief-tools";
        const hint = document.createElement("div");
        hint.className = "relief-hint";
        hint.textContent = words.hint;
        const canvas = document.createElement("canvas");
        canvas.className = "relief-canvas";
        root.append(bar, hint, canvas);
        stage.appendChild(root);

        const context = canvas.getContext("2d");
        const scale = Math.max(4, Math.floor(Math.min(900, stage.clientWidth - 40, stage.clientHeight - 90) / Math.max(rows, cols)));
        canvas.width = cols * scale;
        canvas.height = rows * scale;

        const paintStud = function (index) {
            const row = Math.floor(index / cols);
            const col = index % cols;
            const level = grid[index];
            if (heightsOnly) {
                // From pale to dark, whatever the picture's colours; grey where it can't change.
                const light = 92 - (70 * level) / maxHeight;
                context.fillStyle = border[index] ? "#b9bdc4" : `hsl(215, 45%, ${light}%)`;
                context.fillRect(col * scale, row * scale, scale, scale);
                return;
            }
            context.fillStyle = palette[colors[index]];
            context.fillRect(col * scale, row * scale, scale, scale);
            if (level) {
                context.fillStyle = `rgba(230, 20, 40, ${0.12 + (0.55 * level) / maxHeight})`;
                context.fillRect(col * scale, row * scale, scale, scale);
            }
        };
        const paintAll = function () {
            for (let index = 0; index < grid.length; index++) {
                paintStud(index);
            }
        };

        const toolButtons = {};
        const button = function (label, onClick) {
            const element = document.createElement("button");
            element.type = "button";
            element.textContent = label;
            element.addEventListener("click", onClick);
            bar.appendChild(element);
            return element;
        };
        const pick = function (name) {
            tool = name;
            for (const [key, element] of Object.entries(toolButtons)) {
                element.setAttribute("aria-pressed", String(key === name));
            }
        };
        for (const name of ["raise", "lower", "flatten"]) {
            toolButtons[name] = button(words[name], () => pick(name));
        }
        pick(tool);

        const brushes = document.createElement("label");
        brushes.textContent = `${words.brush} `;
        const select = document.createElement("select");
        for (const size of BRUSHES) {
            const option = document.createElement("option");
            option.value = size;
            option.textContent = String(size);
            option.selected = size === brush;
            select.appendChild(option);
        }
        select.addEventListener("change", () => (brush = Number(select.value)));
        brushes.appendChild(select);
        bar.appendChild(brushes);

        const showHeights = button(words.heightsOnly, function () {
            heightsOnly = !heightsOnly;
            showHeights.setAttribute("aria-pressed", String(heightsOnly));
            paintAll();
        });
        showHeights.setAttribute("aria-pressed", "false");

        const remember = function () {
            undo.push(grid.slice());
            if (undo.length > UNDO_LIMIT) {
                undo.shift();
            }
        };
        button(words.undo, function () {
            const previous = undo.pop();
            if (previous) {
                grid.set(previous);
                paintAll();
            }
        });
        button(words.reset, function () {
            remember();
            grid.fill(0);
            paintAll();
        });
        const applyButton = button(words.apply, async function () {
            applyButton.disabled = true;
            hint.textContent = words.saving;
            try {
                const response = await fetch(url, {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({heights: Array.from(grid)}),
                });
                if (!response.ok) {
                    throw new Error(`relief: ${response.status}`);
                }
                window.dash_clientside.set_props("relief-saved", {data: Date.now()});
                window.dash_clientside.set_props("relief-modal", {opened: false});
            } catch (error) {
                console.error(error);
                hint.textContent = words.failed;
                applyButton.disabled = false;
            }
        });
        applyButton.className = "primary";

        // A stroke changes each stud once, however long the brush lingers on it.
        let touched = null;
        const stroke = function (event) {
            const box = canvas.getBoundingClientRect();
            const col = Math.floor(((event.clientX - box.left) / box.width) * cols);
            const row = Math.floor(((event.clientY - box.top) / box.height) * rows);
            const reach = (brush - 1) / 2;
            for (let r = Math.max(0, row - reach); r <= Math.min(rows - 1, row + reach); r++) {
                for (let c = Math.max(0, col - reach); c <= Math.min(cols - 1, col + reach); c++) {
                    const index = r * cols + c;
                    if (touched.has(index) || border[index]) {
                        continue;
                    }
                    touched.add(index);
                    if (tool === "raise") {
                        grid[index] = Math.min(maxHeight, grid[index] + 1);
                    } else if (tool === "lower") {
                        grid[index] = Math.max(0, grid[index] - 1);
                    } else {
                        grid[index] = 0;
                    }
                    paintStud(index);
                }
            }
        };
        canvas.addEventListener("pointerdown", function (event) {
            remember();
            touched = new Set();
            canvas.setPointerCapture(event.pointerId);
            stroke(event);
        });
        canvas.addEventListener("pointermove", function (event) {
            if (touched) {
                stroke(event);
            }
        });
        const end = () => (touched = null);
        canvas.addEventListener("pointerup", end);
        canvas.addEventListener("pointercancel", end);

        paintAll();
        return {
            grid,
            dispose: () => root.remove(),
        };
    }

    function hide() {
        request += 1;
        if (current) {
            current.dispose();
            current = null;
        }
    }

    async function show(stageId, url, language) {
        hide();
        const mine = request;
        const words = WORDS[language] || WORDS.fr;
        try {
            const stage = await waitForElement(stageId);
            const response = await fetch(url);
            if (!response.ok) {
                throw new Error(`relief: ${response.status}`);
            }
            const data = await response.json();
            if (mine === request) {
                current = open(stage, url, data, words);
            }
        } catch (error) {
            console.error(error);
        }
    }

    return {
        show,
        hide,
        grid: () => current && current.grid,
    };
})();
