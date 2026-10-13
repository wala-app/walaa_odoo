{
    "name": "Walaa",
    "summary": "Sync products and confirmed orders from Odoo to Walaa",
    "version": "20.0.2.4.0",
    "category": "Sales",
    "author": "Walaa",
    "website": "https://walaa.app",
    "license": "LGPL-3",
    "images": [
        "static/description/banner.png",
        "static/description/screenshot_gifts.png",
        "static/description/screenshot_product.png",
        "static/description/screenshot_order.png",
        "static/description/screenshot_actions.png",
        "static/description/screenshot_settings.png",
    ],
    "depends": ["base", "product", "sale", "point_of_sale"],
    "data": [
        "views/res_config_settings_views.xml"
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "walaa_odoo/static/src/js/walaa_pos.js",
            "walaa_odoo/static/src/xml/walaa_pos.xml",
        ],
    },
    "installable": True,
    "application": False,
}
