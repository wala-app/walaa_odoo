{
    "name": "Walaa Connector",
    "summary": "Sync products and confirmed orders from Odoo to Walaa",
    "version": "18.0.1.0.0",
    "category": "Sales",
    "author": "Walaa",
    "license": "LGPL-3",
    "depends": ["base", "product", "sale"],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_cron_data.xml",
        "data/ir_actions_server_data.xml",
        "views/res_config_settings_views.xml",
        "views/walaa_integration_job_views.xml"
    ],
    "installable": True,
    "application": False,
}
