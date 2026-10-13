"use strict";

const API = {
    csrf: "/api/usuarios/csrf/",
    login: "/api/usuarios/login/",
    logout: "/api/usuarios/logout/",
    me: "/api/usuarios/me/",
    mesas: "/api/pedidos/mesas/",
    cuentas: "/api/pedidos/cuentas/",
    retirosBarra: "/api/pedidos/barra/retiros/",
    confirmarRetiroBarra: (pedidoId) =>
        `/api/pedidos/barra/retiros/${pedidoId}/confirmar/`,
    productos: "/api/productos/",
    pedidosPorMesa: "/api/pedidos/mesas/cargas/",
    cargasBarra: "/api/pedidos/barra/cargas/",
    consumoCuenta: (cuentaId) => `/api/pedidos/cuentas/${cuentaId}/consumo/`,
    cancelacionesCuenta: (cuentaId, detalleId) =>
        `/api/pedidos/cuentas/${cuentaId}/detalles/${detalleId}/cancelaciones/`,
    cancelacionesPendientes: "/api/pedidos/cancelaciones/pendientes/",
    notificacionesCancelacion: "/api/pedidos/cancelaciones/notificaciones/",
    resolverCancelacion: (id) => `/api/pedidos/cancelaciones/${id}/resolver/`,
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
let orderContext = null;
let orderProducts = [];
let selectedProducts = new Map();
let orderPending = false;
let orderCatalogLoaded = false;
let orderViewGeneration = 0;
let accountViewGeneration = 0;
let accountContext = null;
let accountReturnFocus = null;
let orderReturnFocus = null;
let cancellationContext = null;
let cancellationReturnFocus = null;
let cancellationViewGeneration = 0;
let cancellationPending = false;
let cancellationsViewGeneration = 0;
let notificationRefreshPending = false;
let notificationRefreshPromise = null;
const accountConsumptionRequests = new Map();
let accountConsumptionRefreshPromise = null;
let accountConsumptionRefreshAccountId = null;
let accountConsumptionStale = false;
const uncertainOrderAccounts = new Set();
const uncertainCancellationRequests = new Map();
const uncertainCancellationResolutions = new Set();
const resolvingCancellationRequests = new Set();
const cancellationNotificationStates = new Map();
const confirmingBarRetirements = new Set();

function uncertainOrdersStorageKey(userId) {
    return `restaurant:uncertain-order-mesas:${userId}`;
}

function uncertainCancellationsStorageKey(userId) {
    return `restaurant:uncertain-cancellations:${userId}`;
}

function restoreUncertainCancellationRequests(userId) {
    uncertainCancellationRequests.clear();
    try {
        const saved = JSON.parse(
            sessionStorage.getItem(uncertainCancellationsStorageKey(userId)) || "[]",
        );
        if (!Array.isArray(saved)) {
            throw new Error("El registro local de cancelaciones inciertas tiene un formato inválido.");
        }
        for (const entry of saved) {
            if (
                !Array.isArray(entry)
                || entry.length !== 2
                || typeof entry[0] !== "string"
                || !/^\d+:\d+$/.test(entry[0])
                || !entry[1]
                || typeof entry[1].clave_idempotencia !== "string"
                || !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
                    .test(entry[1].clave_idempotencia)
                || !isValidQuantity(String(entry[1].cantidad))
                || typeof entry[1].motivo !== "string"
                || !entry[1].motivo.trim()
            ) {
                throw new Error("El registro local de cancelaciones inciertas tiene datos inválidos.");
            }
            uncertainCancellationRequests.set(entry[0], entry[1]);
        }
        return "";
    } catch (error) {
        uncertainCancellationRequests.clear();
        return error.message
            || "No se pudieron recuperar las claves de solicitudes inciertas.";
    }
}

function persistUncertainCancellationRequests() {
    try {
        sessionStorage.setItem(
            uncertainCancellationsStorageKey(currentUser.id),
            JSON.stringify([...uncertainCancellationRequests.entries()]),
        );
        return true;
    } catch {
        return false;
    }
}

function restoreUncertainOrderMesas(userId) {
    uncertainOrderAccounts.clear();
    try {
        const savedIds = JSON.parse(
            sessionStorage.getItem(uncertainOrdersStorageKey(userId)) || "[]",
        );
        if (!Array.isArray(savedIds)) {
            throw new Error("El registro local de envíos inciertos tiene un formato inválido.");
        }
        for (const id of savedIds) {
            if (Number.isSafeInteger(id) && id > 0) {
                uncertainOrderAccounts.add(id);
            }
        }
        return "";
    } catch (error) {
        return error.message
            || "No se pudieron recuperar los bloqueos de envíos inciertos.";
    }
}

function markOrderUncertain(mesaId) {
    uncertainOrderAccounts.add(mesaId);
    try {
        sessionStorage.setItem(
            uncertainOrdersStorageKey(currentUser.id),
            JSON.stringify([...uncertainOrderAccounts]),
        );
    } catch {
        showMessage(
            "El envío no pudo confirmarse y quedó bloqueado en esta página, "
                + "pero el navegador no pudo guardar el bloqueo para recargas.",
            true,
        );
    }
}

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
    if (Array.isArray(data)) {
        return data
            .map((item) => formatApiError(item, ""))
            .filter(Boolean)
            .join(" ") || fallback;
    }
    if (typeof data.detail === "string") return data.detail;
    const messages = Object.entries(data)
        .flatMap(([field, messages]) => {
            const values = Array.isArray(messages) ? messages : [messages];
            return values.map((message) => {
                const label = field === "mesa" ? "Mesa" : field;
                const detail = formatApiError(message, "");
                return detail ? `${label}: ${detail}` : "";
            });
        })
        .filter(Boolean)
        .join(" ");
    return messages || fallback;
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
        const error = new Error(
            formatApiError(data, "No se pudo completar la solicitud."),
        );
        error.status = response.status;
        throw error;
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

function canLoadOrder(cuenta) {
    if (!currentUser) return false;
    if (currentUser.rol === "MOZO") {
        return !cuenta
            || cuenta.mozo_responsable_id === null
            || cuenta.mozo_responsable_id === currentUser.id;
    }
    return currentUser.rol === "BARRA" && Boolean(cuenta);
}

function canPlaceOrder(cuenta) {
    if (!currentUser) return false;
    if (currentUser.rol === "MOZO") {
        return !cuenta
            || cuenta.mozo_responsable_id === null
            || cuenta.mozo_responsable_id === currentUser.id;
    }
    return currentUser.rol === "BARRA"
        && Boolean(cuenta)
        && cuenta.mozo_responsable_id !== null;
}

function canRequestCancellation(cuenta) {
    return Boolean(
        currentUser
        && currentUser.rol === "MOZO"
        && cuenta
        && cuenta.mozo_responsable_id === currentUser.id,
    );
}

function cancellationDetailKey(cuentaId, detailId) {
    return `${cuentaId}:${detailId}`;
}

function generateIdempotencyKey() {
    const cryptoApi = typeof globalThis === "undefined"
        ? null
        : globalThis.crypto;
    if (cryptoApi && typeof cryptoApi.randomUUID === "function") {
        return cryptoApi.randomUUID();
    }
    if (!cryptoApi || typeof cryptoApi.getRandomValues !== "function") {
        throw new Error(
            "Este navegador no permite generar una clave segura para la solicitud. "
                + "Usá un navegador actualizado con soporte de aleatoriedad criptográfica.",
        );
    }

    const bytes = new Uint8Array(16);
    try {
        cryptoApi.getRandomValues(bytes);
    } catch (error) {
        const reason = error instanceof Error && error.message
            ? ` (${error.message})`
            : "";
        throw new Error(
            `No se pudo generar una clave segura para la solicitud${reason}.`,
        );
    }
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = [...bytes].map((byte) => byte.toString(16).padStart(2, "0"));
    return [
        hex.slice(0, 4).join(""),
        hex.slice(4, 6).join(""),
        hex.slice(6, 8).join(""),
        hex.slice(8, 10).join(""),
        hex.slice(10, 16).join(""),
    ].join("-");
}

function reconcileUncertainCancellationRequests(cuentaId, consumption) {
    const foundKeys = new Set();
    for (const pedido of consumption.pedidos) {
        for (const detalle of pedido.detalles) {
            for (const solicitud of detalle.solicitudes_cancelacion || []) {
                foundKeys.add(solicitud.clave_idempotencia);
            }
        }
    }
    const prefix = `${cuentaId}:`;
    let changed = false;
    for (const [detailKey, operation] of uncertainCancellationRequests) {
        if (
            detailKey.startsWith(prefix)
            && foundKeys.has(operation.clave_idempotencia)
        ) {
            uncertainCancellationRequests.delete(detailKey);
            changed = true;
        }
    }
    if (changed) persistUncertainCancellationRequests();
}

function renderTableButton(mesa, cuenta) {
    const button = makeElement("button", "zone-table-button");
    button.type = "button";
    button.append(makeElement("span", "zone-table-number", String(mesa.numero)));
    const uncertain = uncertainOrderAccounts.has(mesa.id);
    const status = !cuenta
        ? "Sin cuenta abierta"
        : `Cuenta abierta · ${cuenta.mozo_responsable_nombre || "Sin responsable"}`;
    button.setAttribute(
        "aria-label",
        `${mesa.identificacion}. ${status}${uncertain
            ? ". Envío anterior sin confirmar; nueva carga bloqueada."
            : ""}`,
    );
    button.dataset.tableId = String(mesa.id);
    button.disabled = !canLoadOrder(cuenta) || uncertain;
    if (!button.disabled) {
        button.addEventListener("click", () => openAccountForm(mesa, cuenta));
    }
    return button;
}

function formatMoney(value) {
    const amount = Number(value);
    if (!Number.isFinite(amount)) return "Precio no disponible";
    return new Intl.NumberFormat("es-AR", {
        style: "currency",
        currency: "ARS",
    }).format(amount);
}

function setOrderMessage(text, isError = false, isUncertain = false) {
    const message = document.getElementById("order-message");
    message.textContent = text;
    message.classList.toggle("message-error", isError);
    message.classList.toggle("order-uncertain", isUncertain);
    message.hidden = !text;
}

function isValidQuantity(value) {
    if (!/^[1-9]\d*$/.test(value)) return false;
    return Number.isSafeInteger(Number(value));
}

function updateOrderTotal() {
    let total = 0;
    let valid = selectedProducts.size > 0;
    let validQuantities = true;
    for (const { product, quantity } of selectedProducts.values()) {
        if (!isValidQuantity(quantity)) {
            valid = false;
            validQuantities = false;
            continue;
        }
        total += Number(product.precio) * Number(quantity);
    }
    document.getElementById("order-total").textContent =
        `Total estimado: ${validQuantities ? formatMoney(total) : "—"}`;
    const uncertain = orderContext
        && uncertainOrderAccounts.has(orderContext.mesa.id);
    const submit = document.getElementById("order-submit");
    submit.disabled = !valid
        || !orderCatalogLoaded
        || orderPending
        || uncertain
        || !orderContext
        || !canPlaceOrder(orderContext.cuenta);
    if (uncertain) {
        submit.textContent = "Resultado sin confirmar";
    } else if (orderPending) {
        submit.textContent = "Enviando…";
    } else {
        submit.textContent = "Enviar pedido";
    }
}

function setOrderPending(pending) {
    orderPending = pending;
    document.getElementById("order-cancel").disabled = pending;
    document.getElementById("order-close").disabled = pending;
    document.getElementById("product-search").disabled = pending;
    document
        .querySelectorAll("#product-catalog button, #order-summary input, #order-summary button")
        .forEach((control) => {
            control.disabled = pending;
        });
    updateOrderTotal();
}

function renderProductCatalog() {
    const container = document.getElementById("product-catalog");
    const query = document.getElementById("product-search").value
        .trim()
        .toLocaleLowerCase("es");
    const filtered = orderProducts.filter((product) =>
        product.nombre.toLocaleLowerCase("es").includes(query),
    );
    container.replaceChildren();
    if (filtered.length === 0) {
        container.append(makeElement(
            "p",
            "catalog-empty",
            query ? "No hay productos que coincidan con la búsqueda."
                : "No hay productos disponibles.",
        ));
        return;
    }

    const categories = new Map();
    for (const product of filtered) {
        const name = product.categoria_nombre || "Sin categoría";
        if (!categories.has(name)) categories.set(name, []);
        categories.get(name).push(product);
    }
    for (const [categoryName, products] of [...categories].sort(([a], [b]) =>
        a.localeCompare(b, "es"),
    )) {
        const section = makeElement("section", "product-category");
        section.append(makeElement("h3", "", categoryName));
        for (const product of products) {
            const row = makeElement("div", "product-choice");
            const info = makeElement("div", "product-info");
            info.append(makeElement("span", "product-name", product.nombre));
            info.append(makeElement("span", "product-price", formatMoney(product.precio)));
            row.append(info);
            const isSelected = selectedProducts.has(product.id);
            const button = makeElement(
                "button",
                "button button-secondary",
                isSelected ? "Agregado" : "Agregar",
            );
            button.type = "button";
            button.disabled = isSelected
                || orderPending
                || !orderContext
                || !canPlaceOrder(orderContext.cuenta);
            button.addEventListener("click", () => addOrderProduct(product));
            row.append(button);
            section.append(row);
        }
        container.append(section);
    }
}

function renderOrderSummary() {
    const summary = document.getElementById("order-summary");
    summary.replaceChildren();
    if (selectedProducts.size === 0) {
        summary.append(makeElement("p", "summary-empty", "Todavía no agregaste productos."));
        updateOrderTotal();
        return;
    }

    for (const [productId, item] of selectedProducts) {
        const row = makeElement("div", "summary-item");
        const info = makeElement("div", "summary-info");
        info.append(makeElement("span", "summary-name", item.product.nombre));
        info.append(makeElement(
            "span",
            "summary-price",
            `${formatMoney(item.product.precio)} c/u`,
        ));
        row.append(info);

        const controls = makeElement("div", "summary-item-controls");
        const quantity = makeElement("input", "summary-quantity");
        quantity.type = "number";
        quantity.min = "1";
        quantity.step = "1";
        quantity.inputMode = "numeric";
        quantity.setAttribute("aria-label", `Cantidad de ${item.product.nombre}`);
        quantity.value = item.quantity;
        quantity.addEventListener("input", () => {
            item.quantity = quantity.value;
            quantity.setCustomValidity(
                isValidQuantity(quantity.value)
                    ? ""
                    : "Ingresá una cantidad entera positiva.",
            );
            updateOrderTotal();
        });
        controls.append(quantity);

        const remove = makeElement("button", "button button-quiet summary-remove", "Quitar");
        remove.type = "button";
        remove.addEventListener("click", () => {
            selectedProducts.delete(productId);
            renderOrderSummary();
            renderProductCatalog();
        });
        controls.append(remove);
        row.append(controls);
        summary.append(row);
    }
    updateOrderTotal();
}

function addOrderProduct(product) {
    if (
        orderPending
        || !orderContext
        || !canPlaceOrder(orderContext.cuenta)
        || selectedProducts.has(product.id)
    ) return;
    selectedProducts.set(product.id, { product, quantity: "1" });
    renderOrderSummary();
    renderProductCatalog();
}

function isCurrentOrderView(generation) {
    return orderViewGeneration === generation
        && Boolean(orderContext)
        && !document.getElementById("order-overlay").hidden;
}

function isCurrentAccountView(generation) {
    return accountViewGeneration === generation
        && Boolean(accountContext)
        && !document.getElementById("account-overlay").hidden;
}

function isCurrentCancellationView(generation) {
    return cancellationViewGeneration === generation
        && Boolean(cancellationContext)
        && !document.getElementById("cancellation-overlay").hidden
        && document.getElementById("account-overlay").hidden;
}

function setAccountMessage(text, isError = false) {
    const message = document.getElementById("account-message");
    message.textContent = text;
    message.classList.toggle("message-error", isError);
    message.hidden = !text;
}

function showNoConsumption() {
    const container = document.getElementById("account-consumption");
    container.replaceChildren(makeElement("p", "summary-empty", "Sin consumos"));
    document.getElementById("consumption-loading").hidden = true;
    const total = document.getElementById("account-total");
    total.textContent = "Total acumulado: $0,00";
    total.hidden = false;
}

function showUnassignedAccountMessage() {
    document.getElementById("account-consumption").replaceChildren(
        makeElement(
            "p",
            "summary-empty",
            "El consumo no está disponible hasta que la cuenta tenga responsable.",
        ),
    );
    document.getElementById("consumption-loading").hidden = true;
    document.getElementById("account-total").hidden = true;
}

function showAccountError(message) {
    document.getElementById("consumption-loading").hidden = true;
    document.getElementById("account-consumption").replaceChildren();
    document.getElementById("account-total").hidden = true;
    setAccountMessage(message, true);
}

function setActiveFormOverlay(activeOverlayId) {
    for (const overlayId of [
        "account-overlay",
        "order-overlay",
        "cancellation-overlay",
    ]) {
        const overlay = document.getElementById(overlayId);
        if (overlay) overlay.inert = overlayId !== activeOverlayId;
    }
}

function canViewCurrentAccountConsumption(cuenta) {
    return Boolean(
        cuenta
        && currentUser
        && (
            currentUser.rol === "BARRA"
            || (
                currentUser.rol === "MOZO"
                && cuenta.mozo_responsable_id === currentUser.id
            )
        ),
    );
}

function fetchAccountConsumption(cuentaId) {
    const pendingRequest = accountConsumptionRequests.get(cuentaId);
    if (pendingRequest) return pendingRequest;

    const request = fetchApi(API.consumoCuenta(cuentaId)).finally(() => {
        if (accountConsumptionRequests.get(cuentaId) === request) {
            accountConsumptionRequests.delete(cuentaId);
        }
    });
    accountConsumptionRequests.set(cuentaId, request);
    return request;
}

async function refreshAccountConsumptionAfterCancellation(cuentaId) {
    if (
        !accountContext
        || !accountContext.cuenta
        || accountContext.cuenta.id !== cuentaId
        || !canViewCurrentAccountConsumption(accountContext.cuenta)
    ) return false;

    accountConsumptionStale = true;
    if (document.getElementById("account-overlay").hidden) {
        return true;
    }

    if (accountConsumptionRefreshPromise) {
        const pendingAccountId = accountConsumptionRefreshAccountId;
        const refreshed = await accountConsumptionRefreshPromise;
        if (
            !accountContext
            || !accountContext.cuenta
            || accountContext.cuenta.id !== cuentaId
            || !canViewCurrentAccountConsumption(accountContext.cuenta)
        ) return false;
        if (document.getElementById("account-overlay").hidden) {
            accountConsumptionStale = true;
            return true;
        }
        if (pendingAccountId === cuentaId) {
            if (refreshed && !accountConsumptionStale) return true;
            if (!refreshed) return false;
        }
    }

    const contextAtStart = accountContext;
    const generation = accountViewGeneration;
    const request = (async () => {
        try {
            const data = await fetchAccountConsumption(cuentaId);
            if (
                accountContext !== contextAtStart
                || accountViewGeneration !== generation
                || !accountContext.cuenta
                || accountContext.cuenta.id !== cuentaId
                || !canViewCurrentAccountConsumption(accountContext.cuenta)
            ) return false;
            if (document.getElementById("account-overlay").hidden) {
                accountConsumptionStale = true;
                return true;
            }
            reconcileUncertainCancellationRequests(cuentaId, data);
            renderAccountConsumption(data);
            accountConsumptionStale = false;
            if (uncertainOrderAccounts.has(accountContext.mesa.id)) {
                updateAccountHeader();
            } else {
                setAccountMessage("");
            }
            return true;
        } catch {
            if (
                accountContext === contextAtStart
                && accountViewGeneration === generation
                && !document.getElementById("account-overlay").hidden
            ) {
                accountConsumptionStale = true;
                setAccountMessage(
                    "No se pudo actualizar el consumo. Se conserva la información mostrada.",
                );
            }
            return false;
        } finally {
            if (accountConsumptionRefreshPromise === request) {
                accountConsumptionRefreshPromise = null;
                accountConsumptionRefreshAccountId = null;
            }
        }
    })();
    accountConsumptionRefreshPromise = request;
    accountConsumptionRefreshAccountId = cuentaId;
    return request;
}

function cancellationStatusLabel(status) {
    return {
        PENDIENTE: "Pendiente",
        ACEPTADA: "Aceptada",
        RECHAZADA: "Rechazada",
    }[status] || "Estado desconocido";
}

function renderCancellationHistory(detail, row) {
    for (const solicitud of detail.solicitudes_cancelacion || []) {
        const item = makeElement("div", `cancellation-history cancellation-${solicitud.estado.toLowerCase()}`);
        const outcome = solicitud.estado === "PENDIENTE"
            ? "sin descontar del consumo"
            : solicitud.estado === "ACEPTADA"
                ? "unidades descontadas"
                : "sin descuento";
        item.append(makeElement(
            "strong",
            "",
            `${cancellationStatusLabel(solicitud.estado)} · ${solicitud.cantidad} u. · ${outcome}`,
        ));
        item.append(makeElement("span", "", `Motivo: ${solicitud.motivo}`));
        if (solicitud.resuelto_por_nombre) {
            item.append(makeElement(
                "span",
                "",
                `Resuelta por: ${solicitud.resuelto_por_nombre}`,
            ));
        }
        row.append(item);
    }
}

function renderAccountConsumption(consumption) {
    const container = document.getElementById("account-consumption");
    container.replaceChildren();
    if (consumption.pedidos.length === 0) {
        container.append(makeElement("p", "summary-empty", "Sin consumos"));
    } else {
        for (const pedido of consumption.pedidos) {
            const order = makeElement("section", "consumption-order");
            const orderHeading = makeElement("div", "consumption-order-heading");
            const authorName = pedido.autor_nombre || "Autor no disponible";
            const date = new Date(pedido.fecha_creacion);
            const createdAt = Number.isNaN(date.getTime())
                ? "Fecha no disponible"
                : new Intl.DateTimeFormat("es-AR", {
                    dateStyle: "short",
                    timeStyle: "short",
                }).format(date);
            orderHeading.append(makeElement("strong", "", `Pedido · ${createdAt}`));
            orderHeading.append(makeElement("span", "", `Autor: ${authorName}`));
            if (pedido.agregado_por_barra) {
                orderHeading.append(makeElement(
                    "span",
                    "consumption-barra-label",
                    `Agregado por barra · ${authorName}`,
                ));
            }
            order.append(orderHeading);
            for (const detalle of pedido.detalles) {
                const row = makeElement("div", "consumption-item");
                const productInfo = makeElement("div", "consumption-product");
                productInfo.append(makeElement(
                    "span",
                    "consumption-product-name",
                    detalle.producto_nombre,
                ));
                productInfo.append(makeElement(
                    "span",
                    "consumption-item-price",
                    `${formatMoney(detalle.precio_unitario)} c/u`,
                ));
                row.append(productInfo);
                row.append(makeElement(
                    "span",
                    "consumption-quantity",
                    `Original: ${detalle.cantidad} · Canceladas: ${
                        detalle.cantidad_cancelada
                    } · Vigentes: ${detalle.cantidad_vigente}`,
                ));
                row.append(makeElement(
                    "strong",
                    "consumption-subtotal",
                    `Vigente: ${formatMoney(detalle.subtotal)}`,
                ));
                if (Number(detalle.subtotal_original) !== Number(detalle.subtotal)) {
                    row.append(makeElement(
                        "span",
                        "consumption-original-subtotal",
                        `Original: ${formatMoney(detalle.subtotal_original)}`,
                    ));
                }
                renderCancellationHistory(detalle, row);
                const operationKey = accountContext
                    ? cancellationDetailKey(accountContext.cuenta.id, detalle.id)
                    : "";
                const uncertainOperation = operationKey
                    ? uncertainCancellationRequests.get(operationKey)
                    : null;
                if (canRequestCancellation(accountContext && accountContext.cuenta)
                    && (uncertainOperation || detalle.cantidad_disponible_cancelacion > 0)) {
                    const cancelButton = makeElement(
                        "button",
                        "button button-quiet cancellation-request-open",
                        uncertainOperation
                            ? "Reintentar solicitud"
                            : "Cancelar",
                    );
                    cancelButton.type = "button";
                    cancelButton.dataset.detailId = String(detalle.id);
                    cancelButton.dataset.productName = detalle.producto_nombre;
                    cancelButton.dataset.available = String(
                        detalle.cantidad_disponible_cancelacion,
                    );
                    cancelButton.dataset.retry = String(Boolean(uncertainOperation));
                    productInfo.append(cancelButton);
                }
                order.append(row);
            }
            container.append(order);
        }
    }
    document.getElementById("consumption-loading").hidden = true;
    const total = document.getElementById("account-total");
    total.textContent = `Total acumulado: ${formatMoney(consumption.total)}`;
    total.hidden = false;
}

function updateAccountHeader() {
    if (!accountContext) return;
    const { mesa, cuenta } = accountContext;
    document.getElementById("account-context").textContent = cuenta
        ? `${mesa.identificacion} · Cuenta #${cuenta.id}`
        : `${mesa.identificacion} · Sin cuenta abierta`;
    const responsible = cuenta
        ? cuenta.mozo_responsable_nombre || "Sin responsable"
        : "Se asignará al enviar el primer pedido";
    document.getElementById("account-responsible").textContent =
        `Responsable: ${responsible}`;
    const uncertain = uncertainOrderAccounts.has(mesa.id);
    const newOrderButton = document.getElementById("new-order-open");
    newOrderButton.hidden = !canPlaceOrder(cuenta) || uncertain;
    newOrderButton.disabled = false;
    if (uncertain) {
        setAccountMessage(
            "Un envío anterior para esta mesa no pudo confirmarse. "
                + "No vuelvas a enviarlo; consultá con el responsable antes de otra carga.",
        );
    } else {
        setAccountMessage("");
    }
}

async function loadAccountConsumption(
    generation = accountViewGeneration,
    failureMessage = "No se pudo consultar el consumo de la cuenta.",
) {
    if (!isCurrentAccountView(generation)) return false;
    const { cuenta } = accountContext;
    if (!cuenta) {
        showNoConsumption();
        return true;
    }
    if (!cuenta.mozo_responsable_id && currentUser.rol === "MOZO") {
        showUnassignedAccountMessage();
        return true;
    }

    const loading = document.getElementById("consumption-loading");
    document.getElementById("account-consumption").replaceChildren();
    document.getElementById("account-total").hidden = true;
    setAccountMessage("");
    loading.hidden = false;
    try {
        const data = await fetchAccountConsumption(cuenta.id);
        if (!isCurrentAccountView(generation)) return false;
        reconcileUncertainCancellationRequests(cuenta.id, data);
        renderAccountConsumption(data);
        accountConsumptionStale = false;
        return true;
    } catch (error) {
        if (!isCurrentAccountView(generation)) return false;
        showAccountError(failureMessage
            ? `${failureMessage} ${error.message || ""}`.trim()
            : error.message || "No se pudo consultar el consumo de la cuenta.");
        return false;
    }
}

function openAccountForm(mesa, cuenta) {
    closeNotificationPanel();
    accountReturnFocus = document.activeElement;
    accountContext = { mesa, cuenta: cuenta || null };
    accountConsumptionStale = false;
    const generation = ++accountViewGeneration;
    document.querySelector(".topbar").inert = true;
    document.querySelector(".page-shell").inert = true;
    document.getElementById("account-overlay").hidden = false;
    document.getElementById("order-overlay").hidden = true;
    setActiveFormOverlay("account-overlay");
    updateAccountHeader();
    document.getElementById("account-consumption").replaceChildren();
    document.getElementById("account-total").hidden = true;
    document.getElementById("consumption-loading").hidden = false;
    setAccountMessage("");
    const firstAction = document.getElementById("new-order-open").hidden
        ? document.getElementById("account-close")
        : document.getElementById("new-order-open");
    firstAction.focus();
    loadAccountConsumption(generation);
}

function closeAccountForm() {
    if (
        document.getElementById("account-overlay").hidden
        || !document.getElementById("order-overlay").hidden
    ) return;
    accountViewGeneration += 1;
    accountContext = null;
    accountConsumptionStale = false;
    document.getElementById("account-overlay").hidden = true;
    setActiveFormOverlay(null);
    document.querySelector(".topbar").inert = false;
    document.querySelector(".page-shell").inert = false;
    if (accountReturnFocus && accountReturnFocus.isConnected) {
        accountReturnFocus.focus();
    } else {
        document.getElementById("refresh-button").focus();
    }
    accountReturnFocus = null;
}

function openCancellationForm(button) {
    if (
        !accountContext
        || !accountContext.cuenta
        || !canRequestCancellation(accountContext.cuenta)
        || document.getElementById("account-overlay").hidden
    ) return;
    closeNotificationPanel();
    const detailId = Number(button.dataset.detailId);
    const available = Number(button.dataset.available);
    const detailKey = cancellationDetailKey(accountContext.cuenta.id, detailId);
    const pendingOperation = uncertainCancellationRequests.get(detailKey) || null;
    const isRetry = button.dataset.retry === "true" && Boolean(pendingOperation);
    if (
        !Number.isSafeInteger(detailId)
        || detailId < 1
        || !Number.isSafeInteger(available)
        || (!isRetry && available < 1)
    ) return;

    let claveIdempotencia = isRetry
        ? pendingOperation.clave_idempotencia
        : null;
    if (!isRetry) {
        try {
            claveIdempotencia = generateIdempotencyKey();
        } catch (error) {
            setAccountMessage(
                error.message || "No se pudo generar una clave segura para la solicitud.",
                true,
            );
            return;
        }
    }

    cancellationReturnFocus = button;
    cancellationContext = {
        cuentaId: accountContext.cuenta.id,
        detailId,
        available,
        detailKey,
        claveIdempotencia,
        retryOperation: isRetry,
    };
    const generation = ++cancellationViewGeneration;
    cancellationPending = false;
    document.getElementById("account-overlay").hidden = true;
    document.getElementById("cancellation-overlay").hidden = false;
    setActiveFormOverlay("cancellation-overlay");
    document.getElementById("cancellation-product").textContent =
        `${button.dataset.productName} · Disponibles para solicitar: ${available}`;
    document.getElementById("cancellation-quantity").max = String(available);
    document.getElementById("cancellation-quantity").value = isRetry
        ? String(pendingOperation.cantidad)
        : "";
    document.getElementById("cancellation-reason").value = isRetry
        ? pendingOperation.motivo
        : "";
    document.getElementById("cancellation-quantity").disabled = isRetry;
    document.getElementById("cancellation-reason").disabled = isRetry;
    document.getElementById("cancellation-submit").disabled = false;
    document.getElementById("cancellation-submit").textContent =
        isRetry ? "Reintentar solicitud" : "Solicitar cancelación";
    document.getElementById("cancellation-message").hidden = true;
    document.getElementById("cancellation-message").textContent = "";
    document.getElementById("cancellation-quantity").focus();
    if (!isCurrentCancellationView(generation)) closeCancellationForm(false);
}

function closeCancellationForm(restoreFocus = true) {
    if (
        document.getElementById("cancellation-overlay").hidden
        || cancellationPending
    ) return;
    cancellationViewGeneration += 1;
    cancellationContext = null;
    document.getElementById("cancellation-overlay").hidden = true;
    document.getElementById("account-overlay").hidden = false;
    setActiveFormOverlay("account-overlay");
    if (
        restoreFocus
        && cancellationReturnFocus
        && cancellationReturnFocus.isConnected
    ) {
        cancellationReturnFocus.focus();
    } else {
        document.getElementById("account-close").focus();
    }
    cancellationReturnFocus = null;
    if (accountConsumptionStale && accountContext && accountContext.cuenta) {
        refreshAccountConsumptionAfterCancellation(accountContext.cuenta.id);
    }
}

async function submitCancellation(event) {
    event.preventDefault();
    if (!cancellationContext || cancellationPending) return;
    const quantity = document.getElementById("cancellation-quantity").value;
    const reason = document.getElementById("cancellation-reason").value.trim();
    const quantityInput = document.getElementById("cancellation-quantity");
    if (
        !cancellationContext.retryOperation
        && (
            !isValidQuantity(quantity)
            || Number(quantity) > cancellationContext.available
            || !reason
        )
    ) {
        showCancellationMessage(
            "Ingresá una cantidad disponible y un motivo obligatorio.",
            true,
        );
        return;
    }

    if (
        !cancellationContext.retryOperation
        && !cancellationContext.claveIdempotencia
    ) {
        try {
            cancellationContext.claveIdempotencia = generateIdempotencyKey();
        } catch (error) {
            showCancellationMessage(
                error.message || "No se pudo generar una clave segura para la solicitud.",
                true,
            );
            return;
        }
    }

    const context = { ...cancellationContext };
    const operation = context.retryOperation
        ? uncertainCancellationRequests.get(context.detailKey)
        : {
            clave_idempotencia: context.claveIdempotencia,
            cantidad: Number(quantity),
            motivo: reason,
        };
    if (!operation) {
        showCancellationMessage(
            "No se encontró la solicitud original para reintentar. Cerrá esta ventana y actualizá la cuenta.",
            true,
        );
        return;
    }
    const previousOperation = uncertainCancellationRequests.get(context.detailKey);
    uncertainCancellationRequests.set(context.detailKey, operation);
    if (!persistUncertainCancellationRequests()) {
        if (previousOperation) {
            uncertainCancellationRequests.set(context.detailKey, previousOperation);
        } else {
            uncertainCancellationRequests.delete(context.detailKey);
        }
        showCancellationMessage(
            "No se pudo guardar la clave de idempotencia en esta sesión. "
                + "La solicitud no se envió.",
            true,
        );
        return;
    }
    const generation = cancellationViewGeneration;
    cancellationPending = true;
    document.getElementById("cancellation-submit").disabled = true;
    document.getElementById("cancellation-submit").textContent = "Enviando…";
    quantityInput.disabled = true;
    document.getElementById("cancellation-reason").disabled = true;
    try {
        await fetchApi(
            API.cancelacionesCuenta(context.cuentaId, context.detailId),
            {
                method: "POST",
                body: JSON.stringify(operation),
            },
        );
        if (!isCurrentCancellationView(generation)) return;
        cancellationPending = false;
        uncertainCancellationRequests.delete(context.detailKey);
        persistUncertainCancellationRequests();
        closeCancellationForm(false);
        const accountGeneration = ++accountViewGeneration;
        document.getElementById("account-consumption").replaceChildren();
        document.getElementById("account-total").hidden = true;
        document.getElementById("consumption-loading").hidden = false;
        await loadAccountConsumption(
            accountGeneration,
            "La solicitud se registró, pero no se pudo actualizar el consumo.",
        );
        document.getElementById("account-close").focus();
    } catch (error) {
        if (!isCurrentCancellationView(generation)) return;
        cancellationPending = false;
        const confirmedRejection = Number.isInteger(error.status)
            && error.status >= 400
            && error.status < 500;
        if (confirmedRejection && error.status !== 409) {
            uncertainCancellationRequests.delete(context.detailKey);
            persistUncertainCancellationRequests();
            cancellationContext.retryOperation = false;
            try {
                cancellationContext.claveIdempotencia = generateIdempotencyKey();
            } catch (keyError) {
                cancellationContext.claveIdempotencia = null;
                document.getElementById("cancellation-submit").disabled = true;
                showCancellationMessage(
                    "La solicitud anterior fue rechazada. No se pudo generar una clave "
                        + "segura para una nueva solicitud; cerrá y volvé a abrir el formulario.",
                    true,
                );
                return;
            }
            quantityInput.disabled = false;
            document.getElementById("cancellation-reason").disabled = false;
            document.getElementById("cancellation-submit").disabled = false;
            document.getElementById("cancellation-submit").textContent =
                "Solicitar cancelación";
        } else {
            cancellationContext.retryOperation = true;
            document.getElementById("cancellation-submit").disabled = false;
            document.getElementById("cancellation-submit").textContent =
                "Reintentar solicitud";
        }
        showCancellationMessage(
            confirmedRejection
                ? error.message || "No se pudo registrar la solicitud."
                : "No se pudo confirmar si la solicitud llegó al sector. "
                    + "No la reenvíes; cerrá esta ventana y actualizá el consumo.",
            true,
        );
    }
}

function showCancellationMessage(text, isError = false) {
    const message = document.getElementById("cancellation-message");
    message.textContent = text;
    message.classList.toggle("message-error", isError);
    message.hidden = !text;
}

function restoreAccountFocus() {
    const preferred = document.getElementById("new-order-open");
    if (preferred && !preferred.hidden && !preferred.disabled) {
        preferred.focus();
        return;
    }
    document.getElementById("account-close").focus();
}

async function openOrderForm() {
    if (!accountContext || !canPlaceOrder(accountContext.cuenta)) return;
    closeNotificationPanel();
    orderReturnFocus = document.activeElement;
    const { mesa, cuenta } = accountContext;
    accountViewGeneration += 1;
    document.getElementById("account-overlay").hidden = true;
    setActiveFormOverlay("order-overlay");
    ++orderViewGeneration;
    orderContext = { mesa, cuenta };
    orderProducts = [];
    selectedProducts = new Map();
    orderPending = false;
    orderCatalogLoaded = false;
    const accountStatus = cuenta
        ? `Cuenta abierta · Responsable: ${
            cuenta.mozo_responsable_nombre || "Sin responsable"
        }`
        : "Sin cuenta abierta · Se abrirá al enviar el pedido";
    document.getElementById("order-context").textContent =
        `${mesa.identificacion} · ${accountStatus}`;
    document.getElementById("product-search").value = "";
    document.getElementById("order-overlay").hidden = false;
    document.getElementById("catalog-loading").hidden = false;
    document.getElementById("catalog-loading").textContent = canPlaceOrder(cuenta)
        ? "Cargando productos…"
        : "Barra no puede cargar productos en una cuenta sin responsable.";
    document.getElementById("product-catalog").replaceChildren();
    document.getElementById("order-summary").replaceChildren(
        makeElement("p", "summary-empty", "Todavía no agregaste productos."),
    );
    setOrderMessage("");
    if (uncertainOrderAccounts.has(mesa.id)) {
        setOrderMessage(
            "Un envío anterior para esta mesa no pudo confirmarse. "
                + "No vuelvas a enviarlo desde esta sesión.",
            false,
            true,
        );
    }
    updateOrderTotal();
    document.getElementById("product-search").focus();

    if (uncertainOrderAccounts.has(mesa.id)) {
        document.getElementById("catalog-loading").hidden = true;
        return;
    }
    const generation = orderViewGeneration;
    try {
        const products = await fetchApi(API.productos);
        if (!isCurrentOrderView(generation)) return;
        orderProducts = products;
        orderCatalogLoaded = true;
        document.getElementById("catalog-loading").hidden = true;
        renderProductCatalog();
        renderOrderSummary();
    } catch (error) {
        if (!isCurrentOrderView(generation)) return;
        document.getElementById("catalog-loading").hidden = true;
        setOrderMessage(
            error.message || "No se pudo cargar el catálogo de productos.",
            true,
        );
        updateOrderTotal();
    }
}

function closeOrderForm(refreshConsumption = true, restoreFocus = true) {
    if (orderPending) return;
    orderViewGeneration += 1;
    document.getElementById("order-overlay").hidden = true;
    setActiveFormOverlay("account-overlay");
    orderContext = null;
    selectedProducts = new Map();
    orderProducts = [];
    orderCatalogLoaded = false;
    document.getElementById("account-overlay").hidden = false;
    updateAccountHeader();
    if (
        restoreFocus
        && orderReturnFocus
        && orderReturnFocus.isConnected
        && !orderReturnFocus.hidden
    ) {
        orderReturnFocus.focus();
    } else if (restoreFocus) {
        restoreAccountFocus();
    }
    orderReturnFocus = null;
    if (refreshConsumption) {
        const generation = ++accountViewGeneration;
        if (accountConsumptionStale && accountContext && accountContext.cuenta) {
            refreshAccountConsumptionAfterCancellation(
                accountContext.cuenta.id,
            );
        } else {
            loadAccountConsumption(generation);
        }
    }
}

async function refreshAccountAfterOrder(mesa) {
    if (
        !accountContext
        || accountContext.mesa.id !== mesa.id
        || document.getElementById("account-overlay").hidden
    ) return;
    const generation = ++accountViewGeneration;
    document.getElementById("new-order-open").disabled = true;
    document.getElementById("account-close").focus();
    document.getElementById("account-consumption").replaceChildren();
    document.getElementById("account-total").hidden = true;
    document.getElementById("consumption-loading").hidden = false;
    const refreshed = await loadTables();
    if (!isCurrentAccountView(generation)) return;
    if (!refreshed) {
        showAccountError(
            "El pedido fue enviado, pero el consumo no pudo actualizarse. "
                + "Volvé a mesas, actualizá los datos y abrí la cuenta nuevamente.",
        );
        return;
    }
    const cuenta = refreshed.cuentas.find((item) => item.mesa === mesa.id) || null;
    if (!cuenta) {
        showAccountError(
            "El pedido fue enviado, pero no se encontró la cuenta abierta para actualizar el consumo. "
                + "Volvé a mesas y actualizá los datos.",
        );
        return;
    }
    accountContext.cuenta = cuenta;
    updateAccountHeader();
    document.getElementById("new-order-open").disabled = true;
    const updated = await loadAccountConsumption(
        generation,
        "El pedido fue enviado, pero no se pudo actualizar el consumo.",
    );
    if (isCurrentAccountView(generation)) {
        document.getElementById("new-order-open").disabled = false;
        if (updated) setAccountMessage("Pedido enviado correctamente.");
    }
}

async function submitOrder() {
    if (!orderContext || orderPending || !orderCatalogLoaded) return;
    if (uncertainOrderAccounts.has(orderContext.mesa.id)) return;
    if (selectedProducts.size === 0) {
        setOrderMessage("Agregá al menos un producto antes de enviar.", true);
        return;
    }
    for (const { quantity } of selectedProducts.values()) {
        if (!isValidQuantity(quantity)) {
            setOrderMessage("Las cantidades deben ser números enteros positivos.", true);
            return;
        }
    }

    setOrderPending(true);

    let token;
    try {
        token = await fetchCsrfToken();
    } catch (error) {
        setOrderPending(false);
        setOrderMessage(
            error.message || "No se pudo preparar el envío; el pedido no fue enviado.",
            true,
        );
        return;
    }

    const { mesa, cuenta } = orderContext;
    const endpoint = currentUser.rol === "BARRA"
        ? API.cargasBarra
        : API.pedidosPorMesa;
    const body = {
        mesa: mesa.id,
        detalles: [...selectedProducts.values()].map(({ product, quantity }) => ({
            producto: product.id,
            cantidad: Number(quantity),
        })),
    };
    if (currentUser.rol === "BARRA") body.cuenta = cuenta.id;
    setOrderMessage("");

    let response;
    try {
        response = await fetch(endpoint, {
            method: "POST",
            credentials: "same-origin",
            headers: {
                Accept: "application/json",
                "Content-Type": "application/json",
                "X-CSRFToken": token,
            },
            body: JSON.stringify(body),
        });
    } catch {
        markOrderUncertain(mesa.id);
        setOrderPending(false);
        setOrderMessage(
            "No se pudo confirmar si el pedido fue recibido. No lo reenvíes; "
                + "consultá con el responsable antes de intentar otra carga.",
            false,
            true,
        );
        return;
    }

    const data = await readJson(response);
    if (response.status >= 500) {
        markOrderUncertain(mesa.id);
        setOrderPending(false);
        setOrderMessage(
            "El servidor tuvo un error y no se pudo confirmar si el pedido fue recibido. "
                + "No lo reenvíes; consultá con el responsable antes de intentar otra carga.",
            false,
            true,
        );
        return;
    }
    if (!response.ok) {
        if (response.status === 401 || response.status === 403) {
            await handleAuthenticationFailure();
        }
        setOrderPending(false);
        setOrderMessage(
            formatApiError(data, "El servidor rechazó el pedido."),
            true,
        );
        return;
    }

    setOrderPending(false);
    closeOrderForm(false, false);
    document.getElementById("account-close").focus();
    await refreshAccountAfterOrder(mesa);
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
        const grid = makeElement("div", "zone-table-list");
        for (const mesa of zoneTables) {
            grid.append(renderTableButton(mesa, accountsByTable.get(mesa.id)));
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
        return { mesas, cuentas };
    } catch (error) {
        loading.textContent = "No se pudieron cargar los datos.";
        showMessage(error.message || "No se pudieron cargar mesas y cuentas.", true);
        return null;
    }
}

function setBarRetirementMessage(text, isError = false) {
    const message = document.getElementById("bar-retirements-message");
    message.textContent = text;
    message.classList.toggle("message-error", isError);
    message.hidden = !text;
}

function renderBarRetirements(requests) {
    const container = document.getElementById("bar-retirements-list");
    container.replaceChildren();
    if (!requests.length) {
        container.append(makeElement(
            "p",
            "summary-empty",
            "No hay pedidos pendientes de Cocina.",
        ));
        return;
    }

    const sectorLabels = { PIZZA: "Pizza", PLATOS: "Platos" };
    for (const request of requests) {
        const card = makeElement("article", "cancellation-card");
        const header = makeElement("header", "cancellation-card-header");
        header.append(makeElement("h3", "", `Pedido #${request.pedido}`));
        card.append(header);
        card.append(makeElement("p", "", request.identificacion));
        card.append(makeElement("p", "", `Mozo: ${request.mozo_nombre}`));
        card.append(makeElement("h4", "", "Productos de Cocina"));
        const products = makeElement("ul", "bar-retirement-products");
        for (const product of request.productos_cocina || []) {
            products.append(makeElement(
                "li",
                "",
                `${product.producto_nombre} · ${product.cantidad} unidad(es) · ${
                    sectorLabels[product.sector] || product.sector
                }`,
            ));
        }
        card.append(products);

        const button = makeElement(
            "button",
            "button button-primary",
            "Confirmar recepción y revisión",
        );
        button.type = "button";
        button.dataset.orderId = String(request.pedido);
        button.disabled = (
            !request.puede_habilitar_retiro
            || confirmingBarRetirements.has(request.pedido)
        );
        card.append(button);

        const barraPreparation = (request.estados_sectores || []).find(
            (preparation) => preparation.sector === "BARRA",
        );
        if (barraPreparation && barraPreparation.estado !== "LISTO") {
            card.append(makeElement(
                "p",
                "",
                "Los productos de Barra deben estar listos antes de avisar al mozo.",
            ));
        }
        container.append(card);
    }
}

async function loadBarRetirements(successMessage = "") {
    const loading = document.getElementById("bar-retirements-loading");
    loading.hidden = false;
    setBarRetirementMessage("");
    document.getElementById("bar-retirements-list").replaceChildren();
    try {
        const requests = await fetchApi(API.retirosBarra);
        renderBarRetirements(requests);
        loading.hidden = true;
        if (successMessage) setBarRetirementMessage(successMessage);
        return true;
    } catch (error) {
        loading.hidden = true;
        setBarRetirementMessage(
            error.message || "No se pudieron consultar los retiros pendientes.",
            true,
        );
        return false;
    }
}

async function confirmBarRetirement(button) {
    const pedidoId = Number(button.dataset.orderId);
    if (
        !Number.isSafeInteger(pedidoId)
        || pedidoId < 1
        || button.disabled
        || confirmingBarRetirements.has(pedidoId)
    ) return;

    confirmingBarRetirements.add(pedidoId);
    button.disabled = true;
    try {
        await fetchApi(API.confirmarRetiroBarra(pedidoId), { method: "POST" });
        await loadBarRetirements("Retiro confirmado y mozo notificado.");
    } catch (error) {
        await loadBarRetirements();
        setBarRetirementMessage(
            error.message || "No se pudo confirmar el retiro.",
            true,
        );
    } finally {
        confirmingBarRetirements.delete(pedidoId);
    }
}

function isCurrentCancellationList(generation) {
    return cancellationsViewGeneration === generation
        && document.body.dataset.page === "cancelaciones";
}

function renderCancellationRequests(requests) {
    const container = document.getElementById("cancellation-list");
    container.replaceChildren();
    if (!requests.length) {
        container.append(makeElement(
            "p",
            "summary-empty",
            "No hay solicitudes pendientes.",
        ));
        return;
    }

    for (const request of requests) {
        const card = makeElement("article", "cancellation-card");
        const header = makeElement("header", "cancellation-card-header");
        header.append(makeElement("h2", "", request.producto_nombre));
        header.append(makeElement(
            "span",
            "cancellation-status cancellation-pendiente",
            cancellationStatusLabel(request.estado),
        ));
        card.append(header);
        card.append(makeElement(
            "p",
            "",
            `${request.zona} · Mesa ${request.mesa_numero}`,
        ));
        const requestedAt = new Date(request.fecha_solicitud);
        const requestedAtLabel = Number.isNaN(requestedAt.getTime())
            ? "Fecha no disponible"
            : new Intl.DateTimeFormat("es-AR", {
                dateStyle: "short",
                timeStyle: "short",
            }).format(requestedAt);
        card.append(makeElement("p", "", `Solicitada: ${requestedAtLabel}`));
        card.append(makeElement(
            "p",
            "",
            `${request.cantidad} unidad(es) · Solicita ${request.solicitante_nombre}`,
        ));
        card.append(makeElement("p", "", `Motivo: ${request.motivo}`));
        if (canReviewCancellationRequests()) {
            const actions = makeElement("div", "cancellation-review-actions");
            const button = makeElement("button", "button button-primary", "Aceptar");
            button.type = "button";
            button.dataset.requestId = String(request.id);
            button.dataset.accountId = String(request.cuenta_id);
            button.dataset.state = "ACEPTADA";
            button.disabled = (
                uncertainCancellationResolutions.has(request.id)
                || resolvingCancellationRequests.has(request.id)
            );
            actions.append(button);
            card.append(actions);
        }
        container.append(card);
    }
}

async function loadCancellationRequests(successMessage = "") {
    const generation = ++cancellationsViewGeneration;
    const loading = document.getElementById("cancellations-loading");
    loading.hidden = false;
    loading.textContent = "Cargando solicitudes…";
    document.getElementById("cancellation-list").replaceChildren();
    showMessage("");
    try {
        const requests = await fetchApi(API.cancelacionesPendientes);
        if (!isCurrentCancellationList(generation)) return;
        for (const request of requests) {
            cancellationNotificationStates.set(Number(request.id), {
                estado: request.estado,
                cuenta_id: request.cuenta_id,
            });
        }
        renderCancellationRequests(requests);
        loading.hidden = true;
        if (successMessage) showMessage(successMessage);
        return true;
    } catch (error) {
        if (!isCurrentCancellationList(generation)) return;
        loading.hidden = true;
        showMessage(
            successMessage
                ? `${successMessage} La lista no se pudo actualizar. ${
                    error.message || ""
                }`.trim()
                : error.message || "No se pudieron consultar las solicitudes.",
            true,
        );
        return false;
    }
}

async function resolveCancellation(button) {
    const requestId = Number(button.dataset.requestId);
    const state = button.dataset.state;
    if (
        !canReviewCancellationRequests()
        || !Number.isSafeInteger(requestId)
        || requestId < 1
        || state !== "ACEPTADA"
        || button.disabled
        || resolvingCancellationRequests.has(requestId)
        || uncertainCancellationResolutions.has(requestId)
    ) return;
    const card = button.closest(".cancellation-card, .notification-item");
    const actions = card.querySelectorAll("button");
    const notification = cancellationNotificationStates.get(requestId);
    const buttonAccountId = Number(button.dataset.accountId);
    const accountId = Number.isSafeInteger(buttonAccountId)
        ? buttonAccountId
        : notification
            ? Number(notification.cuenta_id)
            : null;
    resolvingCancellationRequests.add(requestId);
    actions.forEach((action) => {
        action.disabled = true;
    });
    let postFailed = false;
    let postError;
    try {
        await fetchApi(API.resolverCancelacion(requestId), {
            method: "POST",
            body: JSON.stringify({ estado: "ACEPTADA" }),
        });
    } catch (error) {
        postFailed = true;
        postError = error;
    }

    resolvingCancellationRequests.delete(requestId);
    if (postFailed) {
        uncertainCancellationResolutions.add(requestId);
        const notificationsFetched = await refreshCancellationNotifications(
            true,
            accountId,
        );
        const stateVerified = (
            notificationsFetched
            && !uncertainCancellationResolutions.has(requestId)
        );
        if (document.body.dataset.page === "cancelaciones") {
            await loadCancellationRequests();
        }
        showNotificationError(
            stateVerified
                ? "No se pudo confirmar la aceptación. Se verificó el estado de la solicitud. "
                    + (postError.message || "")
                : notificationsFetched
                    ? "No se pudo verificar el estado de la solicitud; la aceptación permanece bloqueada. "
                        + (postError.message || "")
                    : "No se pudo confirmar la aceptación ni consultar su estado; "
                        + "la acción permanece bloqueada. "
                        + (postError.message || ""),
        );
        return;
    }

    const isCancellationPage =
        document.body.dataset.page === "cancelaciones";
    let cancellationListUpdated = true;
    if (isCancellationPage) {
        cancellationListUpdated = Boolean(
            await loadCancellationRequests("Solicitud aceptada."),
        );
    }
    const notificationsUpdated = await refreshCancellationNotifications(
        true,
        accountId,
    );
    if (!cancellationListUpdated || !notificationsUpdated) {
        showNotificationError(
            "Solicitud aceptada, pero no se pudo actualizar toda la información. "
                + "Actualizá la vista para consultar el estado actual.",
        );
    }
}

function canReviewCancellationRequests() {
    return Boolean(
        currentUser
        && currentUser.rol === "BARRA",
    );
}

function setNotificationError(text) {
    const element = document.getElementById("notification-error");
    if (!element) return;
    element.textContent = text;
    element.hidden = !text;
    const syncError = document.getElementById("notification-sync-error");
    if (syncError) {
        syncError.textContent = text ? "No se pudo actualizar" : "";
        syncError.hidden = !text;
    }
}

function showNotificationError(text) {
    const element = document.getElementById("notification-error");
    if (!element) return;
    element.textContent = text;
    element.hidden = false;
    const syncError = document.getElementById("notification-sync-error");
    if (syncError) {
        syncError.textContent = "No se pudo actualizar";
        syncError.hidden = false;
    }
}

function notificationDateLabel(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime())
        ? "Fecha no disponible"
        : new Intl.DateTimeFormat("es-AR", {
            dateStyle: "short",
            timeStyle: "short",
        }).format(date);
}

function notificationStatusText(request) {
    const statusLabel = cancellationStatusLabel(request.estado);
    if (request.estado === "PENDIENTE") {
        return currentUser.rol === "MOZO"
            ? "Pendiente de aprobación"
            : statusLabel;
    }
    return request.resuelto_por_nombre
        ? `${statusLabel} · ${request.resuelto_por_nombre}`
        : statusLabel;
}

function renderCancellationNotifications(requests) {
    const list = document.getElementById("notification-list");
    const count = document.getElementById("notification-count");
    const activeButton = document.activeElement
        && document.activeElement.closest("button[data-request-id]");
    const focusedRequestId = activeButton && list.contains(activeButton)
        ? activeButton.dataset.requestId
        : null;
    list.replaceChildren();
    if (!requests.length) {
        list.append(makeElement(
            "p",
            "notification-empty",
            "No hay notificaciones de cancelación.",
        ));
    }

    const pendingCount = requests.filter(
        (request) => request.estado === "PENDIENTE",
    ).length;
    const countValue = canReviewCancellationRequests()
        ? pendingCount
        : requests.length;
    count.textContent = String(countValue);
    count.hidden = countValue === 0;

    for (const request of requests) {
        const item = makeElement("article", "notification-item");
        const heading = makeElement("div", "notification-item-heading");
        heading.append(makeElement("strong", "", request.producto_nombre));
        heading.append(makeElement(
            "span",
            `notification-status notification-status-${request.estado.toLowerCase()}`,
            notificationStatusText(request),
        ));
        item.append(heading);
        item.append(makeElement(
            "p",
            "",
            `${request.cantidad} unidad(es) · ${request.zona} · Mesa ${request.mesa_numero}`,
        ));
        item.append(makeElement(
            "p",
            "",
            `${request.solicitante_nombre} · ${notificationDateLabel(request.fecha_solicitud)}`,
        ));
        item.append(makeElement("p", "", `Motivo: ${request.motivo}`));
        if (
            canReviewCancellationRequests()
            && request.estado === "PENDIENTE"
        ) {
            const button = makeElement("button", "button button-primary", "Aceptar");
            button.type = "button";
            button.dataset.requestId = String(request.id);
            button.dataset.accountId = String(request.cuenta_id);
            button.dataset.state = "ACEPTADA";
            button.disabled = (
                uncertainCancellationResolutions.has(request.id)
                || resolvingCancellationRequests.has(request.id)
            );
            item.append(button);
        }
        list.append(item);
    }

    if (focusedRequestId) {
        const replacement = [...list.querySelectorAll("button[data-request-id]")]
            .find((button) => button.dataset.requestId === focusedRequestId);
        if (replacement) replacement.focus();
    }
}

async function refreshCancellationNotifications(
    force = false,
    forceAccountId = null,
) {
    if (
        document.visibilityState !== "visible"
        || !currentUser
        || !["MOZO", "BARRA"].includes(currentUser.rol)
    ) return false;
    if (notificationRefreshPending) {
        const currentRefresh = notificationRefreshPromise;
        if (currentRefresh) await currentRefresh;
        if (force) {
            return refreshCancellationNotifications(true, forceAccountId);
        }
        return true;
    }

    notificationRefreshPending = true;
    notificationRefreshPromise = (async () => {
        try {
            const requests = await fetchApi(API.notificacionesCancelacion);
            const changedAccountIds = new Set();
            for (const request of requests) {
                const requestId = Number(request.id);
                const previous = cancellationNotificationStates.get(requestId);
                if (
                    previous
                    && previous.estado !== request.estado
                    && Number.isSafeInteger(Number(request.cuenta_id))
                ) {
                    changedAccountIds.add(Number(request.cuenta_id));
                }
                cancellationNotificationStates.set(requestId, {
                    estado: request.estado,
                    cuenta_id: request.cuenta_id,
                });
            }
            if (Number.isSafeInteger(forceAccountId) && forceAccountId > 0) {
                changedAccountIds.add(forceAccountId);
            }
            const unverifiedRequestIds = [];
            for (const requestId of uncertainCancellationResolutions) {
                const request = requests.find(
                    (candidate) => Number(candidate.id) === requestId,
                );
                if (
                    request
                    && ["PENDIENTE", "ACEPTADA", "RECHAZADA"].includes(
                        request.estado,
                    )
                ) {
                    uncertainCancellationResolutions.delete(requestId);
                } else {
                    unverifiedRequestIds.push(requestId);
                }
            }
            renderCancellationNotifications(requests);
            if (unverifiedRequestIds.length) {
                showNotificationError(
                    "No se pudo verificar el estado de una o más solicitudes; "
                        + "sus aceptaciones permanecen bloqueadas.",
                );
            } else {
                setNotificationError("");
            }
            for (const cuentaId of changedAccountIds) {
                await refreshAccountConsumptionAfterCancellation(cuentaId);
            }
            return true;
        } catch (error) {
            if (Number.isSafeInteger(forceAccountId) && forceAccountId > 0) {
                await refreshAccountConsumptionAfterCancellation(forceAccountId);
            }
            showNotificationError(
                `No se pudieron actualizar las notificaciones. ${
                    error.message || ""
                }`.trim(),
            );
            return false;
        } finally {
            notificationRefreshPending = false;
            notificationRefreshPromise = null;
        }
    })();
    return notificationRefreshPromise;
}

function initializeCancellationNotifications() {
    const bell = document.getElementById("notification-toggle");
    if (!bell) return;
    if (!["MOZO", "BARRA"].includes(currentUser.rol)) {
        bell.closest(".notification-bell").hidden = true;
        return;
    }
    const panel = document.getElementById("notification-panel");
    bell.addEventListener("click", () => {
        const expanded = bell.getAttribute("aria-expanded") === "true";
        if (expanded) {
            closeNotificationPanel();
            return;
        }
        panel.inert = false;
        panel.hidden = false;
        bell.setAttribute("aria-expanded", "true");
    });
    document.getElementById("notification-close").addEventListener("click", () => {
        closeNotificationPanel();
    });
    panel.addEventListener("click", (event) => {
        const button = event.target.closest("button[data-request-id]");
        if (button) resolveCancellation(button);
    });
    refreshCancellationNotifications();
    window.setInterval(
        refreshCancellationNotifications,
        5000,
    );
    document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible") {
            refreshCancellationNotifications(true);
        }
    });
}

function closeNotificationPanel() {
    const bell = document.getElementById("notification-toggle");
    const panel = document.getElementById("notification-panel");
    if (!bell || !panel) return;
    if (panel.contains(document.activeElement) && !bell.closest("[inert]")) {
        bell.focus();
    }
    panel.hidden = true;
    panel.inert = true;
    bell.setAttribute("aria-expanded", "false");
}

function initializeLogin() {
    const form = document.getElementById("login-form");
    form.addEventListener("submit", submitLogin);
}

async function initializeOperationalPage() {
    document.getElementById("logout-button").addEventListener("click", logout);
    const user = await loadCurrentUser();
    if (!user) return;
    const uncertainRestoreError = restoreUncertainOrderMesas(user.id);
    const cancellationRestoreError =
        restoreUncertainCancellationRequests(user.id);
    initializeCancellationNotifications();

    if (document.body.dataset.page === "mesas") {
        document.getElementById("cancellation-nav").hidden =
            user.rol !== "BARRA";
        const barRetirements = document.getElementById("bar-retirements");
        barRetirements.hidden = user.rol !== "BARRA";
        if (user.rol === "BARRA") {
            document.getElementById("bar-retirements-refresh").addEventListener(
                "click",
                () => loadBarRetirements(),
            );
            document.getElementById("bar-retirements-list").addEventListener(
                "click",
                (event) => {
                    const button = event.target.closest("button[data-order-id]");
                    if (button) confirmBarRetirement(button);
                },
            );
        }
        initializeOrderForm();
        document.getElementById("refresh-button").addEventListener("click", loadTables);
        await loadTables();
        if (user.rol === "BARRA") await loadBarRetirements();
    }
    if (document.body.dataset.page === "cancelaciones") {
        const tablesNav = document.getElementById("tables-nav");
        tablesNav.hidden = user.rol !== "BARRA";
        document.getElementById("cancellations-refresh").addEventListener(
            "click",
            loadCancellationRequests,
        );
        document.getElementById("cancellation-list").addEventListener(
            "click",
            (event) => {
                const button = event.target.closest("button[data-request-id]");
                if (button) resolveCancellation(button);
            },
        );
        await loadCancellationRequests();
    }
    if (uncertainRestoreError) showMessage(uncertainRestoreError, true);
    if (cancellationRestoreError) showMessage(cancellationRestoreError, true);
}

function initializeCancellationForm() {
    document.getElementById("cancellation-close").addEventListener(
        "click",
        () => closeCancellationForm(),
    );
    document.getElementById("cancellation-cancel").addEventListener(
        "click",
        () => closeCancellationForm(),
    );
    document.getElementById("cancellation-form").addEventListener(
        "submit",
        submitCancellation,
    );
    document.getElementById("cancellation-overlay").addEventListener(
        "click",
        (event) => {
            if (event.target === event.currentTarget) closeCancellationForm();
        },
    );
}

function initializeOrderForm() {
    document.getElementById("order-close").addEventListener("click", closeOrderForm);
    document.getElementById("order-cancel").addEventListener("click", closeOrderForm);
    document.getElementById("new-order-open").addEventListener("click", openOrderForm);
    document.getElementById("account-close").addEventListener("click", closeAccountForm);
    document.getElementById("account-consumption").addEventListener(
        "click",
        (event) => {
            const button = event.target.closest(".cancellation-request-open");
            if (button) openCancellationForm(button);
        },
    );
    initializeCancellationForm();
    document.getElementById("order-form").addEventListener("submit", (event) => {
        event.preventDefault();
        submitOrder();
    });
    document.getElementById("product-search").addEventListener("input", renderProductCatalog);
    document.getElementById("account-overlay").addEventListener("click", (event) => {
        if (event.target === event.currentTarget) closeAccountForm();
    });
    document.getElementById("order-overlay").addEventListener("click", (event) => {
        if (event.target === event.currentTarget) closeOrderForm();
    });
    document.addEventListener("keydown", (event) => {
        const orderOverlay = document.getElementById("order-overlay");
        const cancellationOverlay = document.getElementById("cancellation-overlay");
        const accountOverlay = document.getElementById("account-overlay");
        if (event.key === "Escape") {
            if (!orderOverlay.hidden) {
                event.preventDefault();
                closeOrderForm();
            } else if (!cancellationOverlay.hidden) {
                event.preventDefault();
                closeCancellationForm();
            } else if (!accountOverlay.hidden) {
                event.preventDefault();
                closeAccountForm();
            }
            return;
        }
        if (event.key !== "Tab") return;
        const activeOverlay = !orderOverlay.hidden
            ? orderOverlay
            : !cancellationOverlay.hidden
                ? cancellationOverlay
                : !accountOverlay.hidden
                    ? accountOverlay
                    : null;
        if (!activeOverlay) return;
        const focusable = [...activeOverlay.querySelectorAll(
            'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        )].filter((element) => !element.hidden && element.getClientRects().length);
        if (!focusable.length) {
            event.preventDefault();
            return;
        }
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && (document.activeElement === first
            || !activeOverlay.contains(document.activeElement))) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && (document.activeElement === last
            || !activeOverlay.contains(document.activeElement))) {
            event.preventDefault();
            first.focus();
        }
    });
}

document.addEventListener("DOMContentLoaded", () => {
    if (document.body.dataset.page === "login") {
        initializeLogin();
    } else {
        initializeOperationalPage();
    }
});
