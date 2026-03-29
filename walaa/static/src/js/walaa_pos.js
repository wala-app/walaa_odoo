/** @odoo-module */
console.log("[Walaa] POS gift module loading...");

import { patch } from "@web/core/utils/patch";
import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { Component, useState, useEffect } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { usePos } from "@point_of_sale/app/store/pos_hook";
import { Dialog } from "@web/core/dialog/dialog";

/**
 * Strip spaces, dashes, parentheses from phone so it arrives clean
 * e.g. "+968 912 34567" => "+96891234567"
 */
function cleanPhone(raw) {
    if (!raw) return "";
    return raw.replace(/[\s\-\(\)]/g, "");
}

// ─── Extend PosOrder to carry multiple Walaa gifts ──────────────────────────

patch(PosOrder.prototype, {
    setup() {
        super.setup(...arguments);
        this.walaaUsedGifts = []; // array of gift objects
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

    confirm() {
        const chosen = this.props.gifts.filter((g) => this.state.selectedIds.has(g.id));
        this.props.onConfirm(chosen);
        this.props.close();
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
}

// ─── Patch ProductScreen to watch for partner changes & show gift popup ─────

patch(ProductScreen.prototype, {
    setup() {
        super.setup(...arguments);

        const pos = usePos();
        const dialog = useService("dialog");

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

                fetch("/walaa/pos/customer_gifts", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        jsonrpc: "2.0",
                        method: "call",
                        params: { customer_phone: phone },
                    }),
                })
                .then((r) => r.json())
                .then((data) => data.result)
                .then((result) => {
                    console.log("[Walaa] Gifts response:", result);
                    if (result && Array.isArray(result.gifts) && result.gifts.length > 0) {
                        const order = pos.get_order();
                        dialog.add(WalaaGiftDialog, {
                            gifts: result.gifts,
                            alreadySelected: order?.walaaUsedGifts || [],
                            onConfirm: (chosen) => {
                                const currentOrder = pos.get_order();
                                if (currentOrder) {
                                    currentOrder.walaaUsedGifts = chosen;
                                    if (typeof currentOrder.save_to_db === "function") {
                                        currentOrder.save_to_db();
                                    }
                                    if (typeof currentOrder.trigger === "function") {
                                        currentOrder.trigger("change");
                                    }
                                    console.log("[Walaa] Gifts selected:", chosen);
                                }
                            },
                        });
                    }
                }).catch((err) => {
                    console.error("[Walaa] Failed to fetch gifts:", err);
                });
            },
            () => [pos.get_order()?.get_partner()]
        );
    },
});

console.log("[Walaa] POS gift module loaded successfully");
