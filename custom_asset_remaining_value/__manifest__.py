# -*- coding: utf-8 -*-
{
    'name': 'Custom Asset Remaining Value',
    'version': '1.0',
    'category': 'Accounting',
    'summary': 'Modify asset remaining value computation on the depreciation board to include salvage value.',
    'depends': ['account_asset'],
    'data': [
        'views/account_asset_views.xml',
    ],
    'installable': True,
    'license': 'LGPL-3',
}
