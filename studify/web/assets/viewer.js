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
    const START_TILT = 0.6; // Radians away from straight on.
    const START_TURN = -0.35;
    const MAX_TILT = 1.45;
    const LARGE_MOSAIC = 96; // Studs along the longest side.
    const PANEL_STUDS = 16;
    const PANEL_HEIGHT = 1.6; // 1 1/3 bricks.
    const FRAME_WIDTH = 1;
    const FRAME_BELOW = PANEL_HEIGHT + PLATE_HEIGHT; // Down to the plates under the panels' edge.
    const FRAME_ABOVE = 1.6; // A course of bricks and its tiles.

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

    function instances(THREE, scene, geometry, count, castShadow, fill) {
        const material = new THREE.MeshStandardMaterial({roughness: 0.45, metalness: 0});
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

    function panelsAndFrame(THREE, scene, box, rows, cols, place) {
        // The panels under the mosaic, then its brick frame, simplified to one solid course
        // per panel length. The back, its pins and its hangers don't show from the front.
        const panels = [];
        for (let row = 0; row < rows; row += PANEL_STUDS) {
            for (let col = 0; col < cols; col += PANEL_STUDS) {
                panels.push([col + PANEL_STUDS / 2, row + PANEL_STUDS / 2]);
            }
        }
        const lengths = [];
        const middle = (FRAME_ABOVE - FRAME_BELOW) / 2;
        const height = FRAME_ABOVE + FRAME_BELOW;
        const along = function (start, end, fixed, across) {
            for (let from = start; from < end; from += PANEL_STUDS) {
                const to = Math.min(from + PANEL_STUDS, end);
                lengths.push(across ? [fixed, (from + to) / 2, FRAME_WIDTH, to - from] : [(from + to) / 2, fixed, to - from, FRAME_WIDTH]);
            }
        };
        const half = FRAME_WIDTH / 2;
        // Top and bottom run the whole width; the sides fit between them.
        along(-FRAME_WIDTH, cols + FRAME_WIDTH, -half, false);
        along(-FRAME_WIDTH, cols + FRAME_WIDTH, rows + half, false);
        along(0, rows, -half, true);
        along(0, rows, cols + half, true);

        return [
            instances(THREE, scene, box, panels.length, true, function (i, matrix, color) {
                const [x, z] = panels[i];
                place(matrix, x, -PANEL_HEIGHT / 2, z, PANEL_STUDS - SEAM, PANEL_HEIGHT, PANEL_STUDS - SEAM);
                color.set(SUPPORT_COLOR);
            }),
            instances(THREE, scene, box, lengths.length, true, function (i, matrix, color) {
                const [x, z, wide, deep] = lengths[i];
                place(matrix, x, middle, z, wide - SEAM, height, deep - SEAM);
                color.set(SUPPORT_COLOR);
            }),
        ];
    }

    function buildScene(THREE, data) {
        const {rows, cols, palette, colors, heights, regions, plates, supports} = data;
        const scene = new THREE.Scene();
        scene.background = new THREE.Color(0xeef0f3);
        const stud = (row, col) => row * cols + col;
        const hex = (row, col) => palette[colors[stud(row, col)]];
        const position = (x, y, z) => new THREE.Vector3(x - cols / 2, y, z - rows / 2);
        const noTurn = new THREE.Quaternion();
        const box = new THREE.BoxGeometry(1, 1, 1);
        const parts = [box];

        const plateCount = plates.length / 4;
        const place = (matrix, x, y, z, width, height, depth) =>
            matrix.compose(position(x, y, z), noTurn, new THREE.Vector3(width, height, depth));

        const meshes = [];
        if (data.assembly === "panels") {
            meshes.push(...panelsAndFrame(THREE, scene, box, rows, cols, place));
        } else {
            const baseplate = new THREE.Mesh(
                box,
                new THREE.MeshStandardMaterial({color: BASEPLATE_COLOR, roughness: 0.6})
            );
            baseplate.scale.set(cols, PLATE_HEIGHT, rows);
            baseplate.position.y = -PLATE_HEIGHT / 2;
            baseplate.receiveShadow = true;
            scene.add(baseplate);
            parts.push(baseplate.material);
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
        for (let row = 0; row < rows; row++) {
            for (let col = 0; col < cols; col++) {
                if (regions[stud(row, col)] < 0) {
                    tilesAt.push(row, col);
                }
            }
        }
        const pegs = function (spots, radius, height, segments) {
            const geometry = new THREE.CylinderGeometry(radius, radius, height, segments);
            parts.push(geometry);
            return instances(THREE, scene, geometry, spots.length / 2, false, function (i, matrix, color) {
                const row = spots[i * 2];
                const col = spots[i * 2 + 1];
                const under = heights[stud(row, col)] * PLATE_HEIGHT;
                const y = under + height / 2;
                matrix.compose(position(col + 0.5, y, row + 0.5), noTurn, new THREE.Vector3(1, 1, 1));
                color.set(hex(row, col));
            });
        };
        if (tilesAt.length) {
            meshes.push(pegs(tilesAt, TILE_RADIUS, PLATE_HEIGHT, Math.max(rows, cols) > LARGE_MOSAIC ? 10 : 20));
        }
        return {scene, meshes, parts};
    }

    function open(THREE, stage, data) {
        const {scene, meshes, parts} = buildScene(THREE, data);
        const span = Math.max(data.rows, data.cols);

        scene.add(new THREE.HemisphereLight(0xffffff, 0x8a8f99, 1.5));
        const sun = new THREE.DirectionalLight(0xffffff, 2.2);
        sun.position.set(-span * 0.6, span * 0.9, -span * 0.6);
        sun.castShadow = true;
        sun.shadow.camera.left = sun.shadow.camera.bottom = -span * 0.8;
        sun.shadow.camera.right = sun.shadow.camera.top = span * 0.8;
        sun.shadow.camera.far = span * 3;
        sun.shadow.bias = -0.0005;
        scene.add(sun);

        const renderer = new THREE.WebGLRenderer({antialias: true});
        renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        renderer.shadowMap.enabled = true;
        renderer.shadowMap.autoUpdate = false; // Nothing moves but the camera: one pass will do.
        renderer.shadowMap.needsUpdate = true;
        const shadowSize = Math.min(4096, renderer.capabilities.maxTextureSize);
        sun.shadow.mapSize.set(shadowSize, shadowSize);
        stage.appendChild(renderer.domElement);

        const camera = new THREE.PerspectiveCamera(35, 1, 0.5, span * 12);
        const startDistance = span * 2.2;
        const view = {tilt: START_TILT, turn: START_TURN, distance: startDistance};

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
                renderer.render(scene, camera);
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

        const canvas = renderer.domElement;
        let last = null;
        const down = function (event) {
            last = {x: event.clientX, y: event.clientY};
            canvas.setPointerCapture(event.pointerId);
            canvas.classList.add("dragging");
        };
        const move = function (event) {
            if (!last) {
                return;
            }
            view.turn -= (event.clientX - last.x) * 0.006;
            view.tilt = Math.min(MAX_TILT, Math.max(0.02, view.tilt - (event.clientY - last.y) * 0.006));
            last = {x: event.clientX, y: event.clientY};
            draw();
        };
        const up = function () {
            last = null;
            canvas.classList.remove("dragging");
        };
        const wheel = function (event) {
            event.preventDefault();
            view.distance = Math.min(span * 5, Math.max(span * 0.15, view.distance * Math.exp(event.deltaY * 0.001)));
            draw();
        };
        const reset = function () {
            Object.assign(view, {tilt: START_TILT, turn: START_TURN, distance: startDistance});
            draw();
        };
        canvas.addEventListener("pointerdown", down);
        canvas.addEventListener("pointermove", move);
        canvas.addEventListener("pointerup", up);
        canvas.addEventListener("pointercancel", up);
        canvas.addEventListener("wheel", wheel, {passive: false});
        canvas.addEventListener("dblclick", reset);
        resize();

        return {
            view,
            draw,
            dispose: function () {
                observer.disconnect();
                meshes.forEach(function (mesh) {
                    mesh.material.dispose();
                    mesh.dispose();
                });
                parts.forEach(function (part) {
                    part.dispose();
                });
                sun.shadow.dispose();
                renderer.dispose();
                renderer.forceContextLoss();
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

    async function show(stageId, url) {
        hide();
        const mine = request;
        let stage = null;
        try {
            stage = await waitForElement(stageId);
            setMessage(stage, "Préparation de la vue 3D…");
            const [THREE, response] = await Promise.all([import(THREE_URL), fetch(url)]);
            if (!response.ok) {
                throw new Error(`model: ${response.status}`);
            }
            const data = await response.json();
            if (mine !== request) {
                return;
            }
            current = open(THREE, stage, data);
            setMessage(stage, "");
        } catch (error) {
            console.error(error);
            if (stage && mine === request) {
                setMessage(stage, "Vue 3D impossible : recharge la page, ou essaie un autre navigateur.");
            }
        }
    }

    return {
        show,
        hide,
        state: function () {
            return current && current.view;
        },
    };
})();
