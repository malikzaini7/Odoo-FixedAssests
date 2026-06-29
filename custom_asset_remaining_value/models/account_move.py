# -*- coding: utf-8 -*-
from odoo import api, fields, models

class AccountMove(models.Model):
    _inherit = 'account.move'

    @api.depends('asset_id', 'depreciation_value', 'asset_id.original_value', 'asset_id.already_depreciated_amount_import', 'state', 'asset_id.method', 'asset_id.value_residual', 'asset_id.book_value')
    def _compute_depreciation_cumulative_value(self):
        self.asset_depreciated_value = 0
        self.asset_remaining_value = 0

        # Protect all the records being assigned, because the assignments invoke method write() on
        # non-protected records, which may cause an infinite recursion in case method write()
        # needs to read one of these fields.
        fields_to_protect = [self._fields['asset_remaining_value'], self._fields['asset_depreciated_value']]
        with self.env.protecting(fields_to_protect, self.asset_id.depreciation_move_ids):
            for asset in self.asset_id:
                if asset.method == 'strict_degressive':
                    depreciated = 0
                    remaining = asset.value_residual if not asset.currency_id.is_zero(asset.value_residual) else asset.book_value
                else:
                    depreciated = asset.already_depreciated_amount_import
                    remaining = asset.original_value - asset.already_depreciated_amount_import
                for move in asset.depreciation_move_ids.sorted(lambda mv: (mv.date, mv._origin.id)):
                    if move.state != 'cancel':
                        remaining -= move.depreciation_value
                        depreciated += move.depreciation_value
                    move.asset_remaining_value = remaining
                    move.asset_depreciated_value = depreciated
