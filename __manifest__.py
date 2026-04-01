{
    "name": "Walaa",
    "summary": "Sync products and confirmed orders from Odoo to Walaa",
    "version": "18.0.2.3.13",
    "category": "Sales",
    "author": "Walaa",
    "license": "LGPL-3",
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
