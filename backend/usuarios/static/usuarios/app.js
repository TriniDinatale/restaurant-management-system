"use strict";

const API = {
    csrf: "/api/usuarios/csrf/",
    login: "/api/usuarios/login/",
    logout: "/api/usuarios/logout/",
    me: "/api/usuarios/me/",
    mesas: "/api/pedidos/mesas/",
    cuentas: "/api/pedidos/cuentas/",
};

const ZONES = [
    ["SALON", "Salón"],
    ["VEREDA_A", "Vereda A"],
    ["VEREDA_B", "Vereda B"],
];

const ROLE_LABELS = {
    ADMINISTRADOR: "Administrador",
    MOZO: "Mozo",
    BARRA: "Barra",
    COCINA: "Cocina",
};

let csrfToken = "";
let currentUser = null;
let openingTableId = null;

function showMessage(text, isError = false) {
    const message = document.getElementById("page-message");
    if (!message) return;
    message.textContent = text;
    message.classList.toggle("message-error", isError);
    message.hidden = !text;
}

function formatApiError(data, fallback) {
    if (!data) return fallback;
    if (typeof data === "string") return data;
    if (Array.isArray(data)) return data.map(String).join(" ");
    if (typeof data.detail === "string") return data.detail;
    return Object.entries(data)
        .flatMap(([field, messages]) => {
            const values = Array.isArray(messages) ? messages : [messages];
            return values.map((message) => {
                const label = field === "mesa" ? "Mesa" : field;
                return `${label}: ${String(message)}`;
            });
        })
        .join(" ");
}

async function readJson(response) {
    try {
        return await response.json();
    } catch {
        return null;
    }
}

async function fetchCsrfToken() {
    const response = await fetch(API.csrf, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
    });
    const data = await readJson(response);
    if (!response.ok || !data || typeof data.csrfToken !== "string") {
        throw new Error("No se pudo preparar la protección CSRF. Actualizá la página.");
    }
    csrfToken = data.csrfToken;
    return csrfToken;
}

async function fetchApi(url, options = {}) {
    const method = (options.method || "GET").toUpperCase();
    const headers = { Accept: "application/json", ...(options.headers || {}) };
    if (options.body !== undefined) headers["Content-Type"] = "application/json";
    if (!["GET", "HEAD", "OPTIONS", "TRACE"].includes(method)) {
        headers["X-CSRFToken"] = await fetchCsrfToken();
    }

    const response = await fetch(url, {
        ...options,
        method,
        headers,
        credentials: "same-origin",
    });
    const data = await readJson(response);
    if (!response.ok) {
        if (response.status === 401 || response.status === 403) {
            await handleAuthenticationFailure();
        }
        throw new Error(formatApiError(data, "No se pudo completar la solicitud."));
    }
    return data;
}

async function handleAuthenticationFailure() {
    let response;
    try {
        response = await fetch(API.me, {
            credentials: "same-origin",
            headers: { Accept: "application/json" },
        });
    } catch {
        showMessage("No se pudo verificar la sesión. Revisá la conexión e intentá actualizar.", true);
        return;
    }

    if (response.status === 401 || response.status === 403) {
        window.location.assign("/");
        return;
    }
    if (!response.ok) {
        showMessage("No se pudo verificar la sesión. Intentá actualizar.", true);
    }
}

async function submitLogin(event) {
    event.preventDefault();
    showMessage("");
    const form = event.currentTarget;
    const submitButton = form.querySelector('button[type="submit"]');
    const username = form.elements.username.value.trim();
    const password = form.elements.password.value;
    submitButton.disabled = true;
    submitButton.textContent = "Ingresando…";

    try {
        const token = await fetchCsrfToken();
        const response = await fetch(API.login, {
            method: "POST",
            credentials: "same-origin",
            headers: {
                Accept: "application/json",
                "Content-Type": "application/json",
                "X-CSRFToken": token,
            },
            body: JSON.stringify({ username, password }),
        });
        const data = await readJson(response);
        if (!response.ok) {
            throw new Error(formatApiError(data, "No se pudo iniciar sesión."));
        }
        form.reset();
        window.location.assign("/");
    } catch (error) {
        showMessage(error.message || "No se pudo iniciar sesión. Intentá nuevamente.", true);
        submitButton.disabled = false;
        submitButton.textContent = "Ingresar";
    }
}

function roleName(role) {
    return ROLE_LABELS[role] || role;
}

function showUser(user) {
    currentUser = user;
    const label = document.getElementById("user-label");
    if (label) label.textContent = `${user.username} · ${roleName(user.rol)}`;
}

async function loadCurrentUser() {
    try {
        const user = await fetchApi(API.me);
        showUser(user);
        return user;
    } catch (error) {
        showMessage(error.message || "No se pudo cargar el usuario.", true);
        return null;
    }
}

async function logout() {
    const button = document.getElementById("logout-button");
    button.disabled = true;
    try {
        await fetchApi(API.logout, { method: "POST", body: "{}" });
        window.location.assign("/");
    } catch (error) {
        showMessage(error.message || "No se pudo cerrar la sesión.", true);
        button.disabled = false;
    }
}

function makeElement(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
}

function renderTableCard(mesa, cuenta) {
    const card = makeElement("article", "table-card");
    const heading = makeElement("div", "table-card-heading");
    heading.append(makeElement("h2", "", mesa.identificacion));
    const hasAccount = Boolean(cuenta);
    heading.append(
        makeElement(
            "span",
            `status-label${hasAccount ? "" : " status-empty"}`,
            hasAccount ? "Cuenta abierta" : "Sin cuenta",
        ),
    );
    card.append(heading);

    if (hasAccount) {
        const responsible = cuenta.mozo_responsable_nombre || "Sin responsable";
        card.append(makeElement("p", "responsible", `Responsable: ${responsible}`));
    } else if (currentUser && currentUser.rol === "MOZO") {
        const button = makeElement(
            "button",
            "button button-primary",
            openingTableId === mesa.id ? "Abriendo cuenta…" : "Abrir cuenta",
        );
        button.type = "button";
        button.disabled = openingTableId === mesa.id;
        button.addEventListener("click", () => openAccount(mesa, button));
        card.append(button);
    }
    return card;
}

function renderTables(mesas, cuentas) {
    const container = document.getElementById("zones");
    container.replaceChildren();
    if (mesas.length === 0) {
        container.append(makeElement("p", "empty-zone", "Todavía no hay mesas configuradas."));
        document.getElementById("loading-message").hidden = true;
        return;
    }
    const accountsByTable = new Map(cuentas.map((cuenta) => [cuenta.mesa, cuenta]));
    const knownZones = new Map(ZONES);
    const zoneKeys = [...ZONES.map(([key]) => key)];
    for (const mesa of mesas) {
        if (!knownZones.has(mesa.zona)) {
            knownZones.set(mesa.zona, mesa.zona);
            zoneKeys.push(mesa.zona);
        }
    }

    for (const zone of zoneKeys) {
        const zoneTables = mesas
            .filter((mesa) => mesa.zona === zone)
            .sort((left, right) => left.numero - right.numero);
        if (!zoneTables.length) continue;
        const section = makeElement("section", "zone-section");
        const heading = makeElement("div", "zone-heading");
        heading.append(makeElement("h2", "", knownZones.get(zone)));
        heading.append(
            makeElement(
                "span",
                "zone-count",
                `${zoneTables.length} ${zoneTables.length === 1 ? "mesa" : "mesas"}`,
            ),
        );
        section.append(heading);
        const grid = makeElement("div", "table-grid");
        for (const mesa of zoneTables) {
            grid.append(renderTableCard(mesa, accountsByTable.get(mesa.id)));
        }
        section.append(grid);
        container.append(section);
    }
    document.getElementById("loading-message").hidden = true;
}

async function loadTables() {
    const loading = document.getElementById("loading-message");
    loading.hidden = false;
    loading.textContent = "Cargando mesas y cuentas…";
    showMessage("");
    try {
        const [mesas, cuentas] = await Promise.all([
            fetchApi(API.mesas),
            fetchApi(API.cuentas),
        ]);
        renderTables(mesas, cuentas);
        return true;
    } catch (error) {
        loading.textContent = "No se pudieron cargar los datos.";
        showMessage(error.message || "No se pudieron cargar mesas y cuentas.", true);
        return false;
    }
}

async function openAccount(mesa, button) {
    if (openingTableId !== null) return;
    openingTableId = mesa.id;
    button.disabled = true;
    button.textContent = "Abriendo cuenta…";
    showMessage("");
    let resultMessage = "";
    let resultIsError = false;
    try {
        await fetchApi(API.cuentas, {
            method: "POST",
            body: JSON.stringify({ mesa: mesa.id }),
        });
        resultMessage = `Cuenta abierta para ${mesa.identificacion}.`;
    } catch (error) {
        resultMessage = error.message || "No se pudo abrir la cuenta.";
        resultIsError = true;
    } finally {
        openingTableId = null;
        const refreshed = await loadTables();
        if (refreshed) {
            showMessage(resultMessage, resultIsError);
        } else if (button.isConnected) {
            button.disabled = false;
            button.textContent = "Abrir cuenta";
        }
    }
}

function initializeLogin() {
    const form = document.getElementById("login-form");
    form.addEventListener("submit", submitLogin);
}

async function initializeOperationalPage() {
    document.getElementById("logout-button").addEventListener("click", logout);
    const user = await loadCurrentUser();
    if (!user) return;

    if (document.body.dataset.page === "mesas") {
        document.getElementById("refresh-button").addEventListener("click", loadTables);
        await loadTables();
    }
}

document.addEventListener("DOMContentLoaded", () => {
    if (document.body.dataset.page === "login") {
        initializeLogin();
    } else {
        initializeOperationalPage();
    }
});
