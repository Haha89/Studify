// The "Vue 3D" window: draws a mosaic's pieces with Three.js, to look at from any angle.
// Three.js is an ES module, so it is imported here rather than loaded with the page
// (see assets_ignore in app.py), and only when someone opens the viewer.
window.studifyViewer = (function () {
    const THREE_URL = "/assets/vendor/three/three.module.min.js";
    const PLATE_HEIGHT = 0.4; // Of a stud's width: 3.2 mm against 8 mm.
    const TILE_RADIUS = 0.46;
    const SEAM = 0.03; // Gap between plates, so that each one shows.
    const BASEPLATE_COLOR = 0x202226;
    const SUPPORT_COLOR = 0x05131d; // LEGO Black, like the hidden plates' real colour.
    // Black too in the parts list, but lighter here, or they wouldn't show against the panels.
    const HANGER_COLOR = 0x79808a;
    const BACK_PART_COLOR = 0x444a53;
    const PIN_COLOR = 0x9aa1ab;
    const START_TILT = 0.6; // Radians away from straight on.
    const START_TURN = -0.35;
    const MIN_TILT = 0.02;
    const MAX_TILT = Math.PI - 0.02; // Past halfway round, the camera is behind the mosaic.
    const LARGE_MOSAIC = 96; // Studs along the longest side.
    const BASEPLATE_STUDS = 32;
    const PANEL_STUDS = 16;
    const PANEL_HEIGHT = 1.6; // 1 1/3 bricks.
    const FRAME_WIDTH = 1;
    const BRICK_HEIGHT = 1.2;
    // The frame's layers, by their middles: plates under the panels' level, then two courses.
    const PLATE_Y = -PANEL_HEIGHT - PLATE_HEIGHT / 2;
    const LOWER_COURSE_Y = -PANEL_HEIGHT + BRICK_HEIGHT / 2;
    const UPPER_COURSE_Y = LOWER_COURSE_Y + BRICK_HEIGHT;
    const TILE_Y = UPPER_COURSE_Y + BRICK_HEIGHT / 2 + PLATE_HEIGHT / 2;
    const EXPLODED_GAP = 5; // Studs between parts in the exploded view.
    const EXPLODED_ZOOM = 1.45;
    const PINS_PER_JOIN = 3;
    const PIN_RADIUS = 0.24;
    const SPACER_STUDS = 2; // The plate on the back of each panel.
    const CROSSING_STUDS = 2; // The plate where four panels meet.
    const HANGER_WIDTH = 5;
    const HANGER_DEPTH = 3;
    const HANGER_HEIGHT = 0.8;
    const METAL = {metalness: 0.95, roughness: 0.28, envMapIntensity: 1.8};
    const WORDS = {
        fr: {
            front: "Face",
            back: "Dos",
            exploded: "Vue éclatée",
            loading: "Préparation de la vue 3D…",
            failed: "Vue 3D impossible : recharge la page, ou essaie un autre navigateur.",
        },
        en: {
            front: "Front",
            back: "Back",
            exploded: "Exploded view",
            loading: "Preparing the 3D view…",
            failed: "3D view not possible: reload the page, or try another browser.",
        },
    };

    let current = null;
    let request = 0; // Lets a slow load notice that it has been replaced.

    function waitForElement(id) {
        // The modal only creates its contents once it opens.
        return new Promise(function (resolve, reject) {
            const start = performance.now();
            (function look() {
                const element = document.getElementById(id);
                if (element) {
                    resolve(element);
                } else if (performance.now() - start > 5000) {
                    reject(new Error("no viewer stage"));
                } else {
                    requestAnimationFrame(look);
                }
            })();
        });
    }

    function setMessage(stage, text) {
        let message = stage.querySelector(".viewer-message");
        if (!message) {
            message = document.createElement("div");
            message.className = "viewer-message";
            stage.appendChild(message);
        }
        message.textContent = text;
        message.hidden = !text;
    }

    function reflections(THREE, renderer) {
        // Metal looks black with nothing to reflect: give it a soft room, brighter overhead.
        const room = new THREE.Scene();
        room.background = new THREE.Color(0x8d939c);
        const panel = function (color, x, y, z, size) {
            const mesh = new THREE.Mesh(
                new THREE.BoxGeometry(size, size, size),
                new THREE.MeshBasicMaterial({color: new THREE.Color(color).multiplyScalar(6)})
            );
            mesh.position.set(x, y, z);
            room.add(mesh);
            return mesh;
        };
        const meshes = [
            panel(0xffffff, -6, 10, -6, 7),
            panel(0xfff4e0, 9, 6, 2, 4),
            panel(0xdfe8ff, -9, 3, 7, 4),
            panel(0x3a3f46, 0, -10, 0, 12),
        ];
        const generator = new THREE.PMREMGenerator(renderer);
        const target = generator.fromScene(room, 0.04);
        generator.dispose();
        meshes.forEach(function (mesh) {
            mesh.geometry.dispose();
            mesh.material.dispose();
        });
        return target;
    }

    function instances(THREE, scene, geometry, count, castShadow, fill, finish) {
        const material = new THREE.MeshStandardMaterial({roughness: 0.45, metalness: 0, ...finish});
        const mesh = new THREE.InstancedMesh(geometry, material, count);
        const matrix = new THREE.Matrix4();
        const color = new THREE.Color();
        for (let i = 0; i < count; i++) {
            fill(i, matrix, color);
            mesh.setMatrixAt(i, matrix);
            mesh.setColorAt(i, color);
        }
        mesh.castShadow = castShadow;
        mesh.receiveShadow = true;
        scene.add(mesh);
        return mesh;
    }

    function boxes(THREE, scene, box, place, list, hexColor) {
        // Each item is [x, y, z, width, height, depth], in studs, before any exploding.
        return instances(THREE, scene, box, list.length, true, function (i, matrix, color) {
            place(matrix, ...list[i]);
            color.set(hexColor);
        });
    }

    function frameParts(rows, cols) {
        // The frame of LEGO's own panel sets (31197's instructions), side by side: on 2-wide
        // plates half under the panels, a course of bricks with pins into the panels, then
        // a course of 1 x 16s and tiles on top. Its parts add up to ``assembly_parts``.
        const parts = [];
        const out = FRAME_WIDTH / 2;
        const side = function (length, toBox) {
            const run = function (from, to, y, tall, wide) {
                parts.push(toBox((from + to) / 2, y, to - from - SEAM, tall, wide));
            };
            const panels = length / PANEL_STUDS;
            // Plates: a 2 x 12, then 2 x 16s, leaving the corners to the 3 x 3s.
            run(2, 14, PLATE_Y, PLATE_HEIGHT, 2);
            for (let at = 14; at < length - 2; at += PANEL_STUDS) {
                run(at, at + PANEL_STUDS, PLATE_Y, PLATE_HEIGHT, 2);
            }
            for (let panel = 0; panel < panels; panel++) {
                const start = panel * PANEL_STUDS;
                run(start + 2, start + 4, LOWER_COURSE_Y, BRICK_HEIGHT, FRAME_WIDTH); // Brick with pin.
                run(start + 4, start + 12, LOWER_COURSE_Y, BRICK_HEIGHT, FRAME_WIDTH); // 1 x 8.
                run(start + 12, start + 14, LOWER_COURSE_Y, BRICK_HEIGHT, FRAME_WIDTH); // Brick with pin.
                if (panel + 1 < panels) {
                    run(start + 14, start + 18, LOWER_COURSE_Y, BRICK_HEIGHT, FRAME_WIDTH); // 1 x 4.
                }
                run(start, start + PANEL_STUDS, UPPER_COURSE_Y, BRICK_HEIGHT, FRAME_WIDTH); // 1 x 16.
            }
            run(1, 4, TILE_Y, PLATE_HEIGHT, FRAME_WIDTH); // 1 x 3.
            for (let at = 4; at < length - 4; at += 8) {
                run(at, at + 8, TILE_Y, PLATE_HEIGHT, FRAME_WIDTH); // 1 x 8.
            }
            run(length - 4, length - 1, TILE_Y, PLATE_HEIGHT, FRAME_WIDTH); // 1 x 3.
        };
        // Each part's middle, in from the edge: 2-wide plates reach one stud under the panels.
        const inset = (wide) => (wide > FRAME_WIDTH ? out : 0);
        side(cols, (x, y, length, tall, wide) => [x, y, -out + inset(wide), length, tall, wide]);
        side(cols, (x, y, length, tall, wide) => [x, y, rows + out - inset(wide), length, tall, wide]);
        side(rows, (z, y, length, tall, wide) => [-out + inset(wide), y, z, wide, tall, length]);
        side(rows, (z, y, length, tall, wide) => [cols + out - inset(wide), y, z, wide, tall, length]);

        // The corners: a 3 x 3 plate, a corner brick, 1 x 1s and a corner plate on top.
        for (const [x, xIn] of [[-out, 1], [cols + out, -1]]) {
            for (const [z, zIn] of [[-out, 1], [rows + out, -1]]) {
                parts.push([x + xIn, PLATE_Y, z + zIn, 3 - SEAM, PLATE_HEIGHT, 3 - SEAM]);
                parts.push([x + xIn / 2, LOWER_COURSE_Y, z, 2 - SEAM, BRICK_HEIGHT, FRAME_WIDTH - SEAM]);
                parts.push([x, LOWER_COURSE_Y, z + zIn / 2, FRAME_WIDTH - SEAM, BRICK_HEIGHT, 2 - SEAM]);
                parts.push([x, UPPER_COURSE_Y, z, FRAME_WIDTH - SEAM, BRICK_HEIGHT, FRAME_WIDTH - SEAM]);
                parts.push([x + xIn / 2, TILE_Y, z, 2 - SEAM, PLATE_HEIGHT, FRAME_WIDTH - SEAM]);
                parts.push([x, TILE_Y, z + zIn / 2, FRAME_WIDTH - SEAM, PLATE_HEIGHT, 2 - SEAM]);
            }
        }
        return parts;
    }

    function panelsAndFrame(THREE, scene, box, geometry, place) {
        // The panels under the mosaic and what is on their back, then the brick frame.
        const {rows, cols, gap, exploded} = geometry;
        const panels = [];
        const spacers = [];
        const y = -PANEL_HEIGHT / 2;
        const backY = -PANEL_HEIGHT - PLATE_HEIGHT / 2;
        for (let row = 0; row < rows; row += PANEL_STUDS) {
            for (let col = 0; col < cols; col += PANEL_STUDS) {
                const x = col + PANEL_STUDS / 2;
                const z = row + PANEL_STUDS / 2;
                panels.push([x, y, z, PANEL_STUDS - SEAM, PANEL_HEIGHT, PANEL_STUDS - SEAM]);
                spacers.push([x, backY, z, SPACER_STUDS, PLATE_HEIGHT, SPACER_STUDS]);
            }
        }

        const frame = frameParts(rows, cols);

        // Two hangers on the top row, at a quarter and three quarters of the way across.
        const hangers = [0.25, 0.75].map(function (share) {
            const x = Math.floor((cols * share) / PANEL_STUDS) * PANEL_STUDS + PANEL_STUDS / 2;
            const z = HANGER_DEPTH;
            return [x, -PANEL_HEIGHT - HANGER_HEIGHT / 2, z, HANGER_WIDTH, HANGER_HEIGHT, HANGER_DEPTH];
        });

        const meshes = [
            boxes(THREE, scene, box, place, panels, SUPPORT_COLOR),
            boxes(THREE, scene, box, place, spacers, BACK_PART_COLOR),
            boxes(THREE, scene, box, place, frame, SUPPORT_COLOR),
            boxes(THREE, scene, box, place, hangers, HANGER_COLOR),
        ];

        if (!exploded) {
            // These straddle four panels, so have nowhere to sit once they're apart.
            const crossings = [];
            for (let row = PANEL_STUDS; row < rows; row += PANEL_STUDS) {
                for (let col = PANEL_STUDS; col < cols; col += PANEL_STUDS) {
                    crossings.push([col, backY, row, CROSSING_STUDS, PLATE_HEIGHT, CROSSING_STUDS]);
                }
            }
            if (crossings.length) {
                meshes.push(boxes(THREE, scene, box, place, crossings, BACK_PART_COLOR));
            }
            return {meshes, extras: []};
        }

        // Pins sit inside the panels' sides, so only show when the panels are apart.
        const across = cols / PANEL_STUDS;
        const down = rows / PANEL_STUDS;
        const pins = [];
        const pinOffsets = [-PANEL_STUDS / 4, 0, PANEL_STUDS / 4];
        for (let c = 0; c < across; c++) {
            for (let r = 0; r < down; r++) {
                const x = c * (PANEL_STUDS + gap) + PANEL_STUDS / 2;
                const z = r * (PANEL_STUDS + gap) + PANEL_STUDS / 2;
                for (let n = 0; n < PINS_PER_JOIN; n++) {
                    if (c + 1 < across) {
                        pins.push({x: x + PANEL_STUDS / 2 + gap / 2, z: z + pinOffsets[n], alongX: true});
                    }
                    if (r + 1 < down) {
                        pins.push({x: x + pinOffsets[n], z: z + PANEL_STUDS / 2 + gap / 2, alongX: false});
                    }
                }
            }
        }
        if (!pins.length) {
            return {meshes, extras: []};
        }
        const pinGeometry = new THREE.CylinderGeometry(PIN_RADIUS, PIN_RADIUS, gap + 2, 10);
        const alongX = new THREE.Quaternion().setFromEuler(new THREE.Euler(0, 0, Math.PI / 2));
        const alongZ = new THREE.Quaternion().setFromEuler(new THREE.Euler(Math.PI / 2, 0, 0));
        const unit = new THREE.Vector3(1, 1, 1);
        const left = (across * PANEL_STUDS + (across - 1) * gap) / 2;
        const far = (down * PANEL_STUDS + (down - 1) * gap) / 2;
        meshes.push(
            instances(THREE, scene, pinGeometry, pins.length, true, function (i, matrix, color) {
                const pin = pins[i];
                matrix.compose(new THREE.Vector3(pin.x - left, y, pin.z - far), pin.alongX ? alongX : alongZ, unit);
                color.set(PIN_COLOR);
            })
        );
        return {meshes, extras: [pinGeometry]};
    }

    function buildScene(THREE, data, exploded, shine) {
        const {rows, cols, palette, colors, heights, regions, plates, supports, metallic} = data;
        const scene = new THREE.Scene();
        scene.background = new THREE.Color(0xeef0f3);
        const onPanels = data.assembly === "panels";
        const cell = onPanels ? PANEL_STUDS : BASEPLATE_STUDS;
        const gap = exploded ? EXPLODED_GAP : 0;
        // Each panel or baseplate moves away from the middle as one, with what is on it.
        const moved = (value) => value + gap * Math.floor(value / cell);
        const width = cols + gap * (Math.ceil(cols / cell) - 1);
        const depth = rows + gap * (Math.ceil(rows / cell) - 1);
        const stud = (row, col) => row * cols + col;
        const hex = (row, col) => palette[colors[stud(row, col)]];
        const position = (x, y, z) => new THREE.Vector3(moved(x) - width / 2, y, moved(z) - depth / 2);
        const noTurn = new THREE.Quaternion();
        const box = new THREE.BoxGeometry(1, 1, 1);
        const parts = [box];

        const plateCount = plates.length / 4;
        const place = (matrix, x, y, z, wide, tall, deep) =>
            matrix.compose(position(x, y, z), noTurn, new THREE.Vector3(wide, tall, deep));

        const meshes = [];
        if (onPanels) {
            const {meshes: under, extras} = panelsAndFrame(
                THREE,
                scene,
                box,
                {rows, cols, gap, exploded},
                place
            );
            meshes.push(...under);
            parts.push(...extras);
        } else {
            const baseplates = [];
            for (let row = 0; row < rows; row += BASEPLATE_STUDS) {
                for (let col = 0; col < cols; col += BASEPLATE_STUDS) {
                    baseplates.push([
                        col + BASEPLATE_STUDS / 2,
                        -PLATE_HEIGHT / 2,
                        row + BASEPLATE_STUDS / 2,
                        BASEPLATE_STUDS - SEAM,
                        PLATE_HEIGHT,
                        BASEPLATE_STUDS - SEAM,
                    ]);
                }
            }
            meshes.push(boxes(THREE, scene, box, place, baseplates, BASEPLATE_COLOR));
        }

        meshes.push(
            instances(THREE, scene, box, plateCount, true, function (i, matrix, color) {
                const [row, col, tall, wide] = plates.slice(i * 4, i * 4 + 4);
                const y = heights[stud(row, col)] * PLATE_HEIGHT + PLATE_HEIGHT / 2;
                place(matrix, col + wide / 2, y, row + tall / 2, wide - SEAM, PLATE_HEIGHT, tall - SEAM);
                color.set(hex(row, col));
            })
        );

        // Hidden plates: only the sides of the outer ones can ever show.
        const supportCount = supports.length / 5;
        if (supportCount) {
            meshes.push(
                instances(THREE, scene, box, supportCount, true, function (i, matrix, color) {
                    const [layer, row, col, tall, wide] = supports.slice(i * 5, i * 5 + 5);
                    const y = (layer - 0.5) * PLATE_HEIGHT;
                    place(matrix, col + wide / 2, y, row + tall / 2, wide - SEAM, PLATE_HEIGHT, tall - SEAM);
                    color.set(SUPPORT_COLOR);
                })
            );
        }

        // Square and rectangular tiles are drawn above; round ones go wherever they aren't.
        const tilesAt = [];
        const metalTilesAt = [];
        const isMetal = new Set(metallic);
        for (let row = 0; row < rows; row++) {
            for (let col = 0; col < cols; col++) {
                if (regions[stud(row, col)] < 0) {
                    (isMetal.has(colors[stud(row, col)]) ? metalTilesAt : tilesAt).push(row, col);
                }
            }
        }
        const pegs = function (spots, radius, height, segments, finish) {
            const geometry = new THREE.CylinderGeometry(radius, radius, height, segments);
            parts.push(geometry);
            return instances(THREE, scene, geometry, spots.length / 2, false, function (i, matrix, color) {
                const row = spots[i * 2];
                const col = spots[i * 2 + 1];
                const under = heights[stud(row, col)] * PLATE_HEIGHT;
                const y = under + height / 2;
                matrix.compose(position(col + 0.5, y, row + 0.5), noTurn, new THREE.Vector3(1, 1, 1));
                color.set(hex(row, col));
            }, finish);
        };
        const segments = Math.max(rows, cols) > LARGE_MOSAIC ? 10 : 20;
        if (tilesAt.length) {
            meshes.push(pegs(tilesAt, TILE_RADIUS, PLATE_HEIGHT, segments));
        }
        if (metalTilesAt.length) {
            meshes.push(pegs(metalTilesAt, TILE_RADIUS, PLATE_HEIGHT, segments, {...METAL, envMap: shine.texture}));
        }
        return {scene, meshes, parts};
    }

    function open(THREE, stage, data, words) {
        const span = Math.max(data.rows, data.cols);

        const sky = new THREE.HemisphereLight(0xffffff, 0xc4c8d0, 1.5);
        const sun = new THREE.DirectionalLight(0xffffff, 2.2);
        sun.position.set(-span * 0.6, span * 0.9, -span * 0.6);
        sun.castShadow = true;
        // Wide enough for the exploded view too.
        sun.shadow.camera.left = sun.shadow.camera.bottom = -span * 1.1;
        sun.shadow.camera.right = sun.shadow.camera.top = span * 1.1;
        sun.shadow.camera.far = span * 3;
        sun.shadow.bias = -0.0005;
        // Lights the back, which the sun never reaches.
        const fill = new THREE.DirectionalLight(0xffffff, 2.2);
        fill.position.set(span * 0.4, -span, span * 0.4);

        const renderer = new THREE.WebGLRenderer({antialias: true});
        const shine = reflections(THREE, renderer);
        let built = buildScene(THREE, data, false, shine);
        built.scene.add(sky, sun, fill);

        renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        renderer.shadowMap.enabled = true;
        renderer.shadowMap.autoUpdate = false; // Nothing moves but the camera: one pass will do.
        renderer.shadowMap.needsUpdate = true;
        const shadowSize = Math.min(4096, renderer.capabilities.maxTextureSize);
        sun.shadow.mapSize.set(shadowSize, shadowSize);
        stage.appendChild(renderer.domElement);

        const camera = new THREE.PerspectiveCamera(35, 1, 0.5, span * 12);
        const startDistance = span * 2.2;
        const view = {tilt: START_TILT, turn: START_TURN, distance: startDistance, exploded: false};

        let drawPending = false;
        const draw = function () {
            if (drawPending) {
                return;
            }
            drawPending = true;
            requestAnimationFrame(function () {
                drawPending = false;
                const flat = Math.sin(view.tilt) * view.distance;
                camera.position.set(
                    Math.sin(view.turn) * flat,
                    Math.cos(view.tilt) * view.distance,
                    Math.cos(view.turn) * flat
                );
                camera.lookAt(0, 0, 0);
                renderer.render(built.scene, camera);
            });
        };

        const resize = function () {
            const width = stage.clientWidth;
            const height = stage.clientHeight;
            if (width && height) {
                renderer.setSize(width, height);
                camera.aspect = width / height;
                camera.updateProjectionMatrix();
                draw();
            }
        };
        const observer = new ResizeObserver(resize);
        observer.observe(stage);

        const freeBuilt = function () {
            built.meshes.forEach(function (mesh) {
                mesh.material.dispose();
                mesh.dispose();
            });
            built.parts.forEach(function (part) {
                part.dispose();
            });
        };

        const look = function (settings) {
            if (settings.exploded !== undefined && settings.exploded !== view.exploded) {
                view.exploded = settings.exploded;
                freeBuilt();
                built = buildScene(THREE, data, view.exploded, shine);
                built.scene.add(sky, sun, fill);
                renderer.shadowMap.needsUpdate = true;
            }
            for (const key of ["tilt", "turn", "distance"]) {
                if (settings[key] !== undefined) {
                    view[key] = settings[key];
                }
            }
            draw();
        };
        const front = () => ({tilt: START_TILT, turn: START_TURN, distance: startDistance});
        // From behind, with the top of the picture and its hangers still at the top.
        const back = () => ({tilt: Math.PI - START_TILT, turn: Math.PI - START_TURN, distance: startDistance});

        const zoomBy = function (factor) {
            view.distance = Math.min(span * 5, Math.max(span * 0.15, view.distance * factor));
            draw();
        };

        const controls = document.createElement("div");
        controls.className = "viewer-controls";
        const button = function (label, onClick) {
            const element = document.createElement("button");
            element.type = "button";
            element.textContent = label;
            element.addEventListener("click", onClick);
            controls.appendChild(element);
            return element;
        };
        button(words.front, () => look(front()));
        button(words.back, () => look(back()));
        const apart = button(words.exploded, function () {
            // Stand back to keep the parts in view as they spread.
            const distance = view.distance * (view.exploded ? 1 / EXPLODED_ZOOM : EXPLODED_ZOOM);
            look({exploded: !view.exploded, distance});
            apart.setAttribute("aria-pressed", String(view.exploded));
        });
        apart.setAttribute("aria-pressed", "false");
        stage.appendChild(controls);

        const canvas = renderer.domElement;
        // Every finger or mouse button on the canvas: one turns, two pinch to zoom.
        const pointers = new Map();
        const spread = function () {
            const [a, b] = pointers.values();
            return Math.hypot(a.x - b.x, a.y - b.y);
        };
        const down = function (event) {
            pointers.set(event.pointerId, {x: event.clientX, y: event.clientY});
            canvas.setPointerCapture(event.pointerId);
            canvas.classList.add("dragging");
        };
        const move = function (event) {
            const last = pointers.get(event.pointerId);
            if (!last) {
                return;
            }
            if (pointers.size === 2) {
                const before = spread();
                pointers.set(event.pointerId, {x: event.clientX, y: event.clientY});
                const after = spread();
                if (before && after) {
                    zoomBy(before / after);
                }
                return;
            }
            view.turn -= (event.clientX - last.x) * 0.006;
            view.tilt = Math.min(MAX_TILT, Math.max(MIN_TILT, view.tilt - (event.clientY - last.y) * 0.006));
            pointers.set(event.pointerId, {x: event.clientX, y: event.clientY});
            draw();
        };
        const up = function (event) {
            pointers.delete(event.pointerId);
            if (!pointers.size) {
                canvas.classList.remove("dragging");
            }
        };
        const wheel = function (event) {
            event.preventDefault();
            zoomBy(Math.exp(event.deltaY * 0.001));
        };
        const reset = () => look(front());
        canvas.addEventListener("pointerdown", down);
        canvas.addEventListener("pointermove", move);
        canvas.addEventListener("pointerup", up);
        canvas.addEventListener("pointercancel", up);
        canvas.addEventListener("wheel", wheel, {passive: false});
        canvas.addEventListener("dblclick", reset);
        resize();

        return {
            view,
            look,
            front,
            back,
            dispose: function () {
                observer.disconnect();
                freeBuilt();
                shine.dispose();
                sun.shadow.dispose();
                renderer.dispose();
                renderer.forceContextLoss();
                controls.remove();
                canvas.remove();
            },
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
        let stage = null;
        try {
            stage = await waitForElement(stageId);
            setMessage(stage, words.loading);
            const [THREE, response] = await Promise.all([import(THREE_URL), fetch(url)]);
            if (!response.ok) {
                throw new Error(`model: ${response.status}`);
            }
            const data = await response.json();
            if (mine !== request) {
                return;
            }
            current = open(THREE, stage, data, words);
            setMessage(stage, "");
        } catch (error) {
            console.error(error);
            if (stage && mine === request) {
                setMessage(stage, words.failed);
            }
        }
    }

    return {
        show,
        hide,
        state: function () {
            return current && current.view;
        },
        look: function (settings) {
            if (current) {
                current.look(settings);
            }
        },
    };
})();
