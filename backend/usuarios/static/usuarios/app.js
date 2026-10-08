"use strict";

const API = {
    csrf: "/api/usuarios/csrf/",
    login: "/api/usuarios/login/",
    logout: "/api/usuarios/logout/",
    me: "/api/usuarios/me/",
    mesas: "/api/pedidos/mesas/",
    cuentas: "/api/pedidos/cuentas/",
    productos: "/api/productos/",
    pedidosPorMesa: "/api/pedidos/mesas/cargas/",
    cargasBarra: "/api/pedidos/barra/cargas/",
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
const uncertainOrderAccounts = new Set();

function uncertainOrdersStorageKey(userId) {
    return `restaurant:uncertain-order-mesas:${userId}`;
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

function canLoadOrder(cuenta) {
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
    button.disabled = !canLoadOrder(cuenta) || uncertain;
    if (!button.disabled) {
        button.addEventListener("click", () => openOrderForm(mesa, cuenta));
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
    submit.disabled = !valid || !orderCatalogLoaded || orderPending || uncertain;
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
            button.disabled = isSelected || orderPending;
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
    if (orderPending || selectedProducts.has(product.id)) return;
    selectedProducts.set(product.id, { product, quantity: "1" });
    renderOrderSummary();
    renderProductCatalog();
}

async function openOrderForm(mesa, cuenta) {
    orderContext = { mesa, cuenta: cuenta || null };
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
    document.getElementById("catalog-loading").textContent = "Cargando productos…";
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

    try {
        const products = await fetchApi(API.productos);
        if (!orderContext || orderContext.mesa.id !== mesa.id) return;
        orderProducts = products;
        orderCatalogLoaded = true;
        document.getElementById("catalog-loading").hidden = true;
        renderProductCatalog();
        renderOrderSummary();
    } catch (error) {
        if (!orderContext || orderContext.mesa.id !== mesa.id) return;
        document.getElementById("catalog-loading").hidden = true;
        setOrderMessage(
            error.message || "No se pudo cargar el catálogo de productos.",
            true,
        );
        updateOrderTotal();
    }
}

function closeOrderForm() {
    if (orderPending) return;
    document.getElementById("order-overlay").hidden = true;
    orderContext = null;
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

    selectedProducts.clear();
    setOrderPending(false);
    closeOrderForm();
    const refreshed = await loadTables();
    showMessage(
        refreshed
            ? "Pedido enviado correctamente. Se actualizaron las mesas."
            : "Pedido enviado correctamente. No se pudo actualizar el listado; "
                + "el pedido quedó confirmado.",
    );
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
        return true;
    } catch (error) {
        loading.textContent = "No se pudieron cargar los datos.";
        showMessage(error.message || "No se pudieron cargar mesas y cuentas.", true);
        return false;
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
    const uncertainRestoreError = restoreUncertainOrderMesas(user.id);

    if (document.body.dataset.page === "mesas") {
        initializeOrderForm();
        document.getElementById("refresh-button").addEventListener("click", loadTables);
        await loadTables();
    }
    if (uncertainRestoreError) showMessage(uncertainRestoreError, true);
}

function initializeOrderForm() {
    document.getElementById("order-close").addEventListener("click", closeOrderForm);
    document.getElementById("order-cancel").addEventListener("click", closeOrderForm);
    document.getElementById("order-form").addEventListener("submit", (event) => {
        event.preventDefault();
        submitOrder();
    });
    document.getElementById("product-search").addEventListener("input", renderProductCatalog);
    document.getElementById("order-overlay").addEventListener("click", (event) => {
        if (event.target === event.currentTarget) closeOrderForm();
    });
    document.addEventListener("keydown", (event) => {
        if (
            event.key === "Escape"
            && !document.getElementById("order-overlay").hidden
        ) {
            closeOrderForm();
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
