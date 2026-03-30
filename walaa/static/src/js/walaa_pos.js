/** @odoo-module */
console.log("[Walaa] POS gift module loading...");

import { patch } from "@web/core/utils/patch";
import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";
import { Component, useState, useEffect } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { usePos } from "@point_of_sale/app/store/pos_hook";
import { Dialog } from "@web/core/dialog/dialog";

const SKIP_REWARD = "__SKIP_REWARD__";

// Gift / reward types as returned by the Walaa API
const GIFT_TYPE_PRODUCT = 0;      // free 1 unit of a specific product
const GIFT_TYPE_NON_PRODUCT = 1;  // free 1 unit, choosable among multiple products
const GIFT_TYPE_DISCOUNT = 2;     // percentage or fixed discount on a product

const DISCOUNT_TYPE_PERCENTAGE = 0;
const DISCOUNT_TYPE_FIXED = 1;

/**
 * Strip spaces, dashes, parentheses from phone so it arrives clean
 * e.g. "+968 912 34567" => "+96891234567"
 */
function cleanPhone(raw) {
    if (!raw) return "";
    return raw.replace(/[\s\-\(\)]/g, "");
}

function getOrderProductChoices(order) {
    const lines = order?.lines || [];
    const seenProductIds = new Set();
    const choices = [];
    for (const line of lines) {
        const product = line?.product_id;
        const productId = product?.id;
        if (!productId || seenProductIds.has(productId)) {
            continue;
        }
        seenProductIds.add(productId);
        const productName =
            (typeof line.get_full_product_name === "function" && line.get_full_product_name()) ||
            product.display_name ||
            product.name ||
            "Product";
        const qty =
            (typeof line.get_quantity === "function" && line.get_quantity()) || line.qty || 0;
        choices.push({
            id: productId,
            label: `${productName} (x${qty})`,
            usedOnProductId: productId,
            usedOnProductName: productName,
            qty,
        });
    }
    return choices;
}

function askProductForGift(dialog, gift, productChoices) {
    return new Promise((resolve) => {
        dialog.add(WalaaGiftProductDialog, {
            giftName: gift?.name || "Gift",
            products: productChoices,
            onConfirm: (product) => resolve(product || false),
            onSkip: () => resolve(SKIP_REWARD),
            close: () => resolve(false),
        });
    });
}

/**
 * Reset any discounts previously applied by Walaa gifts on the order.
 */
function clearGiftDiscounts(order) {
    const applied = new Set(order.walaaGiftAppliedProductIds || []);
    if (!applied.size) return;
    for (const line of order.lines || []) {
        if (applied.has(String(line.product_id?.id))) {
            if (typeof line.set_discount === "function") {
                line.set_discount(0);
            }
        }
    }
    order.walaaGiftAppliedProductIds = [];
}

/**
 * Apply gift effects to matching order lines based on gift type:
 *   PRODUCT / NON_PRODUCT → make 1 unit free (discount = 1/qty * 100%)
 *   DISCOUNT              → apply percentage or fixed discount
 */
function applyGiftsToOrder(order, gifts) {
    clearGiftDiscounts(order);
    if (!gifts?.length) return;

    const appliedProductIds = [];

    for (const gift of gifts) {
        if (!gift.usedOnProductId) continue;

        const line = (order.lines || []).find(
            (l) => String(l.product_id?.id) === String(gift.usedOnProductId)
        );
        if (!line) continue;

        const qty =
            typeof line.get_quantity === "function" ? line.get_quantity() : line.qty || 1;
        let discountPct = 0;

        if (gift.type === GIFT_TYPE_PRODUCT || gift.type === GIFT_TYPE_NON_PRODUCT) {
            // Make usedQty units free: spread over line qty
            const freeUnits = Math.min(gift.usedQty || 1, qty);
            discountPct = (freeUnits / qty) * 100;
        } else if (gift.type === GIFT_TYPE_DISCOUNT && gift.discount) {
            if (gift.discount.type === DISCOUNT_TYPE_PERCENTAGE) {
                discountPct = gift.discount.value;
            } else if (gift.discount.type === DISCOUNT_TYPE_FIXED) {
                const unitPrice =
                    typeof line.get_unit_price === "function"
                        ? line.get_unit_price()
                        : line.price_unit || 0;
                if (unitPrice > 0) {
                    discountPct = Math.min((gift.discount.value / unitPrice) * 100, 100);
                }
            }
        }

        discountPct = Math.min(Math.max(discountPct, 0), 100);

        if (typeof line.set_discount === "function") {
            line.set_discount(discountPct);
            appliedProductIds.push(String(gift.usedOnProductId));
            console.log(
                `[Walaa] Applied ${discountPct.toFixed(2)}% discount to product`,
                gift.usedOnProductId,
                "(gift type:", gift.type, ")"
            );
        }
    }

    order.walaaGiftAppliedProductIds = appliedProductIds;
}

async function fetchCustomerGifts(customerPhone) {
    const response = await fetch("/walaa/pos/customer_gifts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            jsonrpc: "2.0",
            method: "call",
            params: { customer_phone: customerPhone },
        }),
    });
    const data = await response.json();
    return data?.result || null;
}

function openGiftSelectionDialog(dialog, pos, gifts) {
    const order = pos.get_order();
    if (!order || !Array.isArray(gifts) || !gifts.length) {
        return;
    }
    dialog.add(WalaaGiftDialog, {
        gifts,
        alreadySelected: order?.walaaUsedGifts || [],
        onConfirm: async (chosen) => {
            const currentOrder = pos.get_order();
            if (!currentOrder) {
                return;
            }
            const mappedGifts = await mapSelectedGiftsToProducts(
                dialog,
                currentOrder,
                chosen,
                currentOrder.walaaUsedGifts || []
            );
            if (mappedGifts === false) {
                return;
            }
            currentOrder.walaaUsedGifts = mappedGifts;
            applyGiftsToOrder(currentOrder, mappedGifts);
            if (typeof currentOrder.save_to_db === "function") {
                currentOrder.save_to_db();
            }
            if (typeof currentOrder.trigger === "function") {
                currentOrder.trigger("change");
            }
            console.log("[Walaa] Gifts selected and applied:", mappedGifts);
        },
    });
}

async function showCustomerGifts({
    phone,
    pos,
    dialog,
    notification,
    notifyIfEmpty = false,
}) {
    if (!phone) {
        return;
    }
    try {
        const result = await fetchCustomerGifts(phone);
        console.log("[Walaa] Gifts response:", result);
        if (result && Array.isArray(result.gifts) && result.gifts.length > 0) {
            openGiftSelectionDialog(dialog, pos, result.gifts);
            return;
        }
        if (notifyIfEmpty && notification?.add) {
            notification.add("No Walaa gifts available for this customer.", {
                type: "info",
            });
        }
    } catch (err) {
        console.error("[Walaa] Failed to fetch gifts:", err);
        if (notifyIfEmpty && notification?.add) {
            notification.add("Failed to fetch Walaa gifts.", { type: "danger" });
        }
    }
}

async function mapSelectedGiftsToProducts(dialog, order, selectedGifts, alreadyMapped = []) {
    const productChoices = getOrderProductChoices(order);
    if (!selectedGifts?.length) {
        return [];
    }
    if (!productChoices.length) {
        return selectedGifts.map((gift) => ({
            ...gift,
            usedOnProductId: false,
            usedOnProductName: false,
        }));
    }

    const choiceByProductId = new Map(
        productChoices.map((choice) => [String(choice.usedOnProductId), choice])
    );
    const mappedByGiftId = new Map(
        (alreadyMapped || []).map((gift) => [String(gift.id), gift])
    );

    const mapped = [];
    const usedProductIds = new Set();
    for (const gift of selectedGifts) {
        const existing = mappedByGiftId.get(String(gift.id));
        if (existing?.usedOnProductId) {
            const existingProductId = String(existing.usedOnProductId);
            const existingChoice = choiceByProductId.get(existingProductId);
            if (existingChoice && !usedProductIds.has(existingProductId)) {
                usedProductIds.add(existingProductId);
                mapped.push({
                    ...gift,
                    usedOnProductId: existingChoice.usedOnProductId,
                    usedOnProductName:
                        existing.usedOnProductName || existingChoice.usedOnProductName,
                    usedQty: existing.usedQty || 1,
                    lineQty: existingChoice.qty || 1,
                });
                continue;
            }
        }

        const availableChoices = productChoices.filter(
            (choice) => !usedProductIds.has(String(choice.usedOnProductId))
        );
        if (!availableChoices.length) {
            console.warn(
                "[Walaa] No more available products to map for remaining gifts. Remaining gifts are skipped."
            );
            break;
        }

        // Filter to only products allowed for this specific gift
        let filteredChoices = availableChoices;
        const allowedProductIds = gift.productIds || [];
        if (allowedProductIds.length > 0) {
            const allowedSet = new Set(allowedProductIds.map(String));
            filteredChoices = availableChoices.filter((c) =>
                allowedSet.has(String(c.usedOnProductId))
            );
            if (!filteredChoices.length) {
                console.warn("[Walaa] No matching products in order for gift", gift.id, "— skipping");
                continue;
            }
        }

        const selected = await askProductForGift(dialog, gift, filteredChoices);
        if (selected === SKIP_REWARD) {
            continue;
        }
        if (!selected?.usedOnProductId) {
            return false;
        }
        usedProductIds.add(String(selected.usedOnProductId));
        mapped.push({
            ...gift,
            usedOnProductId: selected.usedOnProductId,
            usedOnProductName: selected.usedOnProductName,
            usedQty: 1,
            lineQty: selected.qty || 1,
        });
    }
    return mapped;
}

// ─── Extend PosOrder to carry multiple Walaa gifts ──────────────────────────

patch(PosOrder.prototype, {
    setup() {
        super.setup(...arguments);
        this.walaaUsedGifts = []; // array of gift objects
        this.walaaGiftAppliedProductIds = []; // product ids that have walaa discounts on their lines
        const vals = arguments[0] || {};
        if (vals.used_gifts) {
            try {
                const parsed = JSON.parse(vals.used_gifts);
                this.walaaUsedGifts = Array.isArray(parsed) ? parsed : [];
            } catch {
                this.walaaUsedGifts = [];
            }
        }
    },

    serialize() {
        const json = super.serialize(...arguments);
        if (this.walaaUsedGifts && this.walaaUsedGifts.length > 0) {
            json.used_gifts = JSON.stringify(this.walaaUsedGifts);
        } else {
            json.used_gifts = false;
        }
        console.log("[Walaa] serialize used_gifts:", json.used_gifts);
        return json;
    },
});

// ─── Gift Selection Dialog (multi-select with checkboxes) ───────────────────

class WalaaGiftDialog extends Component {
    static template = "walaa.WalaaGiftDialog";
    static components = { Dialog };
    static props = {
        gifts: Array,
        alreadySelected: Array,
        onConfirm: Function,
        close: Function,
    };

    setup() {
        // Build a Set of already-selected gift IDs
        const selectedIds = new Set((this.props.alreadySelected || []).map((g) => g.id));
        this.state = useState({
            selectedIds,
        });
    }

    toggleGift(gift) {
        if (this.state.selectedIds.has(gift.id)) {
            this.state.selectedIds.delete(gift.id);
        } else {
            this.state.selectedIds.add(gift.id);
        }
        // trigger OWL reactivity by reassigning
        this.state.selectedIds = new Set(this.state.selectedIds);
    }

    isSelected(giftId) {
        return this.state.selectedIds.has(giftId);
    }

    async confirm() {
        const chosen = this.props.gifts.filter((g) => this.state.selectedIds.has(g.id));
        this.props.close();
        await this.props.onConfirm(chosen);
    }

    get selectedCount() {
        return this.state.selectedIds.size;
    }

    formatExpiry(dateStr) {
        if (!dateStr) return null;
        try {
            return new Date(dateStr).toLocaleDateString();
        } catch {
            return dateStr;
        }
    }

    giftTypeBadgeClass(type) {
        if (type === GIFT_TYPE_PRODUCT || type === GIFT_TYPE_NON_PRODUCT) return "badge bg-success ms-2";
        if (type === GIFT_TYPE_DISCOUNT) return "badge bg-info text-dark ms-2";
        return "badge bg-secondary ms-2";
    }

    formatDiscountInfo(gift) {
        if (gift.type !== GIFT_TYPE_DISCOUNT || !gift.discount) return null;
        const { type, value } = gift.discount;
        const formatted = Number(value).toFixed(2);
        return type === DISCOUNT_TYPE_PERCENTAGE ? `${formatted}% off` : `${formatted} off`;
    }
}

class WalaaGiftProductDialog extends Component {
    static template = "walaa.WalaaGiftProductDialog";
    static components = { Dialog };
    static props = {
        giftName: String,
        products: Array,
        onConfirm: Function,
        onSkip: Function,
        close: Function,
    };

    setup() {
        this.state = useState({
            selectedProductId: this.props.products?.[0]?.id || null,
        });
    }

    choose(product) {
        this.state.selectedProductId = product.id;
    }

    isSelected(productId) {
        return String(this.state.selectedProductId) === String(productId);
    }

    confirm() {
        const selected = (this.props.products || []).find(
            (p) => String(p.id) === String(this.state.selectedProductId)
        );
        this.props.onConfirm(selected || false);
        this.props.close();
    }

    skipReward() {
        if (typeof this.props.onSkip === "function") {
            this.props.onSkip();
        }
        this.props.close();
    }
}

patch(ControlButtons.prototype, {
    async onClickWalaaGifts() {
        const order = this.pos.get_order();
        const partner = order?.get_partner?.();
        const phone = cleanPhone(partner?.phone || partner?.mobile);
        if (!phone) {
            this.notification.add("Select a customer with phone number first.", {
                type: "warning",
            });
            return;
        }
        await showCustomerGifts({
            phone,
            pos: this.pos,
            dialog: this.dialog,
            notification: this.notification,
            notifyIfEmpty: true,
        });
        if (typeof this.props?.close === "function") {
            this.props.close();
        }
    },
});

// ─── Patch ProductScreen to watch for partner changes & show gift popup ─────

patch(ProductScreen.prototype, {
    setup() {
        super.setup(...arguments);

        const pos = usePos();
        const dialog = useService("dialog");
        const notification = useService("notification");

        let lastPartnerId = null;

        useEffect(
            (partner) => {
                if (!partner) {
                    lastPartnerId = null;
                    return;
                }
                if (partner.id === lastPartnerId) return;
                lastPartnerId = partner.id;

                const phone = cleanPhone(partner.phone || partner.mobile);
                if (!phone) return;

                console.log("[Walaa] Customer selected with phone:", phone);
                showCustomerGifts({
                    phone,
                    pos,
                    dialog,
                    notification,
                });
            },
            () => [pos.get_order()?.get_partner()]
        );
    },
});

console.log("[Walaa] POS gift module loaded successfully");
