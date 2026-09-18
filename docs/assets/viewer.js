// Shared viewer logic for index.html (live site) and local_index.html (desktop app).
// Only difference between the two pages is the value of window.TEXTURES_API_URL,
// which must return JSON shaped like GitHub's contents API: [{ "name": "..." }, ...]
const canvas = document.getElementById("renderCanvas");
const engine = new BABYLON.Engine(canvas, true);
let scene, camera;

let currentAssetContainer = null;
let currentMaterial = null;
let activeAnimGroup = null;
let currentLoadTaskId = 0;

let isHighPolyMode = true;
let selectedZebraName = "";
let availableZebras = [];
let isSpinning = true;

function log(msg) {
    const l = document.getElementById("log");
    l.innerHTML += `<div>[${new Date().toLocaleTimeString()}] ${msg}</div>`;
    l.scrollTop = l.scrollHeight;
    console.log(`[ZebraViewer] ${msg}`);
}

const createScene = function () {
    const s = new BABYLON.Scene(engine);
    s.clearColor = new BABYLON.Color4(0.1, 0.1, 0.1, 1.0);

    camera = new BABYLON.ArcRotateCamera("camera", Math.PI / 2, Math.PI / 2.1, 3.5, BABYLON.Vector3.Zero(), s);
    camera.attachControl(canvas, true);
    camera.wheelPrecision = 50;

    camera.minZ = 0.01;
    camera.maxZ = 1000;
    camera.lowerRadiusLimit = 0.1;

    const light1 = new BABYLON.HemisphericLight("light1", new BABYLON.Vector3(0, 1, 0), s);
    light1.intensity = 1.2;
    const light2 = new BABYLON.DirectionalLight("light2", new BABYLON.Vector3(-1, -2, -1), s);
    light2.intensity = 0.8;

    s.registerBeforeRender(() => {
        if (isSpinning && camera) {
            camera.alpha += 0.003;
        }
    });

    canvas.addEventListener("pointerdown", () => { isSpinning = false; });

    return s;
};

scene = createScene();

function clearCurrentModel() {
    if (scene) {
        [...scene.animationGroups].forEach(ag => {
            ag.stop();
            ag.dispose();
        });
    }
    activeAnimGroup = null;

    if (currentMaterial) {
        currentMaterial.dispose(true, true);
        currentMaterial = null;
    }

    if (currentAssetContainer) {
        try {
            currentAssetContainer.removeAllFromScene();
            currentAssetContainer.dispose();
        } catch (e) {
            console.warn("Container cleanup error:", e);
        }
        currentAssetContainer = null;
    }

    if (scene) {
        [...scene.meshes].forEach(m => m.dispose(false, true));
        [...scene.transformNodes].forEach(tn => tn.dispose(false, true));
        [...scene.skeletons].forEach(sk => sk.dispose());
    }
}

async function fetchTexturesList() {
    try {
        const response = await fetch(`${window.TEXTURES_API_URL}?t=${Date.now()}`, { cache: "no-store" });
        if (!response.ok) {
            throw new Error(`Texture list request returned ${response.status}`);
        }
        const data = await response.json();

        const namesSet = new Set();
        data.forEach(file => {
            if (file.name.endsWith(".jpg") || file.name.endsWith(".png")) {
                const baseName = file.name.replace("_HighPoly.png", "").replace("_HighPoly.jpg", "").replace(".jpg", "").replace(".png", "");
                namesSet.add(baseName);
            }
        });

        availableZebras = Array.from(namesSet).sort();
        const previousSelection = selectedZebraName;
        selectedZebraName = availableZebras.includes(previousSelection) ? previousSelection : availableZebras[0] || "";
        populateDropdown(availableZebras);

        if (selectedZebraName) {
            loadModelAndTexture();
        }
        log(`Found ${availableZebras.length} zebra texture set(s).`);
    } catch (err) {
        log("Failed to fetch texture list. Using fallback.");
        availableZebras = ["Texture_0001"];
        populateDropdown(availableZebras);
        selectedZebraName = availableZebras[0];
        loadModelAndTexture();
    }
}

function populateDropdown(list) {
    const select = document.getElementById("textureSelect");
    select.innerHTML = "";
    list.forEach(name => {
        const opt = document.createElement("option");
        opt.value = name;
        opt.textContent = name;
        select.appendChild(opt);
    });
    select.value = selectedZebraName;
}

function loadModelAndTexture() {
    currentLoadTaskId++;
    const taskId = currentLoadTaskId;

    clearCurrentModel();

    const modelFile = isHighPolyMode ? "ZebraModel_HighPoly.gltf" : "ZebraModel.gltf";
    const textureFile = isHighPolyMode ? `${selectedZebraName}_HighPoly.png` : `${selectedZebraName}.jpg`;

    log(`Loading ${modelFile}...`);

    BABYLON.SceneLoader.LoadAssetContainer("./assets/model/", modelFile, scene, function (container) {
        if (taskId !== currentLoadTaskId) {
            container.removeAllFromScene();
            container.dispose();
            return;
        }

        clearCurrentModel();

        currentAssetContainer = container;
        container.addAllToScene();
        log(`Loaded ${modelFile} successfully.`);

        // Filter out extra meshes exported together in glTF
        const hasSkeletons = container.skeletons && container.skeletons.length > 0;
        container.meshes.forEach(m => {
            if (isHighPolyMode && hasSkeletons) {
                // In High Poly mode, disable static/unskinned extra meshes
                if (!m.skeleton && m.name !== "__root__") {
                    m.setEnabled(false);
                }
            } else if (!isHighPolyMode && hasSkeletons) {
                // In Low Poly mode, disable skinned animated meshes
                if (m.skeleton) {
                    m.setEnabled(false);
                }
            }
        });

        function frameCameraFromMeshBounds() {
            let min = new BABYLON.Vector3(Number.MAX_VALUE, Number.MAX_VALUE, Number.MAX_VALUE);
            let max = new BABYLON.Vector3(-Number.MAX_VALUE, -Number.MAX_VALUE, -Number.MAX_VALUE);
            let validFound = false;

            container.meshes.forEach(m => {
                if (m.isEnabled() && m.getTotalVertices && m.getTotalVertices() > 0) {
                    m.computeWorldMatrix(true);
                    m.refreshBoundingInfo(true);
                    const bounds = m.getBoundingInfo().boundingBox;
                    min = BABYLON.Vector3.Minimize(min, bounds.minimumWorld);
                    max = BABYLON.Vector3.Maximize(max, bounds.maximumWorld);
                    validFound = true;
                }
            });

            if (validFound) {
                const center = BABYLON.Vector3.Center(min, max);
                const diagonal = BABYLON.Vector3.Distance(min, max);
                camera.setTarget(center);
                camera.radius = Math.max(diagonal * 1.6, 0.75);
                camera.beta = Math.PI / 2.1;
            }
        }

        scene.updateTransformMatrix(true);
        frameCameraFromMeshBounds();

        // Animation Controls
        const animControls = document.getElementById("animControls");
        if (isHighPolyMode && container.animationGroups && container.animationGroups.length > 0) {
            animControls.style.display = "block";
            activeAnimGroup = container.animationGroups[0];
            activeAnimGroup.play(true);
            scene.onAfterRenderObservable.addOnce(frameCameraFromMeshBounds);
        } else {
            animControls.style.display = "none";
        }

        // Create and apply texture material
        const texturePath = `./assets/textures/${textureFile}?v=${Date.now()}`;
        currentMaterial = new BABYLON.StandardMaterial("zebraMat", scene);
        currentMaterial.diffuseTexture = new BABYLON.Texture(texturePath, scene, false, false);
        currentMaterial.diffuseTexture.coordinatesIndex = isHighPolyMode ? 1 : 0;
        currentMaterial.specularColor = new BABYLON.Color3(0.1, 0.1, 0.1);
        currentMaterial.backFaceCulling = false;

        container.meshes.forEach(m => {
            if (m.isEnabled() && m.getTotalVertices && m.getTotalVertices() > 0) {
                m.useVertexColors = false;
                m.material = currentMaterial;
            }
        });
        log(`Applied texture: ${textureFile}`);
    }, null, function (scene, message) {
        if (taskId === currentLoadTaskId) {
            log(`Error loading model: ${message}`);
        }
    });
}

// Event Listeners
document.getElementById("textureSelect").addEventListener("change", (e) => {
    selectedZebraName = e.target.value;
    loadModelAndTexture();
});

document.getElementById("searchInput").addEventListener("input", (e) => {
    const val = e.target.value.toLowerCase();
    const filtered = availableZebras.filter(z => z.toLowerCase().includes(val));
    populateDropdown(filtered);
});

document.getElementById("toggleModelBtn").addEventListener("click", () => {
    isHighPolyMode = !isHighPolyMode;
    const btn = document.getElementById("toggleModelBtn");
    btn.textContent = isHighPolyMode ? "Mode: High-Poly (Animated)" : "Mode: Low-Poly (Static)";
    btn.style.background = isHighPolyMode ? "#28a745" : "#444";
    loadModelAndTexture();
});

document.getElementById("refreshBtn").addEventListener("click", fetchTexturesList);

document.getElementById("playBtn").addEventListener("click", () => {
    if (activeAnimGroup) activeAnimGroup.play(true);
});

document.getElementById("pauseBtn").addEventListener("click", () => {
    if (activeAnimGroup) activeAnimGroup.pause();
});

engine.runRenderLoop(() => scene.render());
window.addEventListener("resize", () => engine.resize());

// Exposed so the desktop app can force a cache-busted reload after a scan completes.
window.zebraViewerRefresh = fetchTexturesList;

fetchTexturesList();
