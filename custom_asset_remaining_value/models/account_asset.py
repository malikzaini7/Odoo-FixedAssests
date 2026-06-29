from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import float_is_zero
from dateutil.relativedelta import relativedelta

class AccountAsset(models.Model):
    _inherit = 'account.asset'

    method = fields.Selection(
        selection_add=[('strict_degressive', 'Strict Declining')],
        ondelete={'strict_degressive': 'set default'}
    )

    @api.constrains('depreciation_move_ids')
    def _check_depreciations(self):
        for asset in self:
            if (
                asset.state == 'open'
                and asset.depreciation_move_ids
                and asset.method != 'strict_degressive'
                and not asset.currency_id.is_zero(
                    asset.depreciation_move_ids.sorted(lambda x: (x.date, x.id))[-1].asset_remaining_value - asset.salvage_value
                )
            ):
                raise UserError(_("The remaining value on the last depreciation line must be equal to salvage value"))

    def set_to_running(self):
        if self.depreciation_move_ids and self.method != 'strict_degressive' and not self.currency_id.is_zero(max(self.depreciation_move_ids, key=lambda m: (m.date, m.id)).asset_remaining_value - self.salvage_value):
            self.env['asset.modify'].create({'asset_id': self.id, 'name': _('Reset to running')}).modify()
        self.write({
            'state': 'open',
            'net_gain_on_sale': 0
        })

    def _get_residual_value_at_date(self, date):
        self.ensure_one()
        current_and_previous_depreciation = self.depreciation_move_ids.filtered(
            lambda mv:
            mv.asset_depreciation_beginning_date < date
            and not mv.reversed_entry_id
        ).sorted('asset_depreciation_beginning_date', reverse=True)
        if not current_and_previous_depreciation:
            return 0

        if len(current_and_previous_depreciation) > 1:
            previous_value_residual = current_and_previous_depreciation[1].asset_remaining_value - self.salvage_value
        else:
            previous_value_residual = self.original_value - self.salvage_value - self.already_depreciated_amount_import

        cur_depr_end_date = self._get_end_period_date(date)
        current_depreciation = current_and_previous_depreciation[0]
        cur_depr_beg_date = current_depreciation.asset_depreciation_beginning_date

        rate = self._get_delta_days(cur_depr_beg_date, date) / self._get_delta_days(cur_depr_beg_date, cur_depr_end_date)
        current_remaining_value = current_depreciation.asset_remaining_value - self.salvage_value
        lost_value_at_date = (previous_value_residual - current_remaining_value) * rate
        residual_value_at_date = self.currency_id.round(previous_value_residual - lost_value_at_date)
        if self.currency_id.compare_amounts(self.original_value, 0) > 0:
            return max(residual_value_at_date, 0)
        else:
            return min(residual_value_at_date, 0)

    def _create_move_before_date(self, date):
        all_move_dates_before_date = (self.depreciation_move_ids.filtered(
            lambda x:
            x.date <= date
            and not x.reversal_move_ids
            and not x.reversed_entry_id
            and x.state == 'posted'
        ).sorted('date')).mapped('date')

        beginning_fiscal_year = self.company_id.compute_fiscalyear_dates(date).get('date_from') if self.method != 'linear' else False
        first_fiscalyear_move = self.env['account.move']
        if all_move_dates_before_date:
            last_move_date_not_reversed = max(all_move_dates_before_date)
            future_moves_beginning_date = self.depreciation_move_ids.filtered(
                lambda m: m.date > last_move_date_not_reversed and (
                    not m.reversal_move_ids and not m.reversed_entry_id and m.state == 'posted'
                    or m.state == 'draft'
                )
            ).mapped('asset_depreciation_beginning_date')
            beginning_depreciation_date = min(future_moves_beginning_date) if future_moves_beginning_date else self.paused_prorata_date

            if self.method != 'linear':
                first_moves = self.depreciation_move_ids.filtered(
                    lambda m: m.asset_depreciation_beginning_date >= beginning_fiscal_year and (
                        not m.reversal_move_ids and not m.reversed_entry_id and m.state == 'posted'
                        or m.state == 'draft'
                    )
                ).sorted(lambda m: (m.asset_depreciation_beginning_date, m.id))
                first_fiscalyear_move = next(iter(first_moves), first_fiscalyear_move)
        else:
            beginning_depreciation_date = self.paused_prorata_date

        residual_declining = first_fiscalyear_move.asset_remaining_value - self.salvage_value + first_fiscalyear_move.depreciation_value if first_fiscalyear_move else 0.0
        self._cancel_future_moves(date)

        imported_amount = self.already_depreciated_amount_import if not all_move_dates_before_date else 0
        value_residual = self.value_residual + self.already_depreciated_amount_import if not all_move_dates_before_date else self.value_residual
        residual_declining = residual_declining or value_residual

        last_day_asset = self._get_last_day_asset()
        lifetime_left = self._get_delta_days(beginning_depreciation_date, last_day_asset)
        days_depreciated, amount = self._compute_board_amount(self.value_residual, beginning_depreciation_date, date, False, lifetime_left, residual_declining, beginning_fiscal_year, lifetime_left, value_residual, beginning_depreciation_date)

        if abs(imported_amount) <= abs(amount):
            amount -= imported_amount
        if not float_is_zero(amount, precision_rounding=self.currency_id.rounding):
            new_line = self._insert_depreciation_line(amount, beginning_depreciation_date, date, days_depreciated)
            new_line._post()

    def _compute_board_amount(self, residual_amount, period_start_date, period_end_date, days_already_depreciated,
                              days_left_to_depreciated, residual_declining, start_yearly_period=None, total_lifetime_left=None,
                              residual_at_compute=None, start_recompute_date=None):
        if self.method == 'strict_degressive':
            if float_is_zero(residual_amount, precision_rounding=self.currency_id.rounding):
                return 0, 0
            # Annual % entered by user → monthly rate = factor / 1200
            monthly_rate = (self.method_progress_factor or 0.0) / 12.0
            # Calculate full-period days for proportional partial amounts
            full_period_days = (period_end_date - period_start_date).days or 1
            amount = self.currency_id.round(residual_amount * monthly_rate)
            amount = min(amount, residual_amount)
            return full_period_days, amount

        return super()._compute_board_amount(
            residual_amount, period_start_date, period_end_date, days_already_depreciated,
            days_left_to_depreciated, residual_declining, start_yearly_period, total_lifetime_left,
            residual_at_compute, start_recompute_date
        )


    def _recompute_board(self, start_depreciation_date=False):
        if self.method == 'strict_degressive':
            self.ensure_one()
            posted_depreciation_move_ids = self.depreciation_move_ids.filtered(
                lambda mv: mv.state == 'posted' and not mv.asset_value_change
            ).sorted(key=lambda mv: (mv.date, mv.id))

            imported_amount = self.already_depreciated_amount_import
            residual_amount = self.value_residual - sum(self.depreciation_move_ids.filtered(lambda mv: mv.state == 'draft').mapped('depreciation_value'))

            depreciation_move_values = []
            
            base_date = self.prorata_date or self.first_depreciation_date or self.acquisition_date
            
            posted_count = len(posted_depreciation_move_ids)
            records_to_generate = int(self.method_number) - posted_count

            remaining_balance = residual_amount if not self.currency_id.is_zero(residual_amount) else self.book_value

            for i in range(records_to_generate):
                months_to_add = int(self.method_period) * (posted_count + i + 1)
                period_end_depreciation_date = base_date + relativedelta(months=months_to_add)
                
                if i == 0 and not posted_depreciation_move_ids:
                    period_start_date = base_date
                else:
                    period_start_date = base_date + relativedelta(months=int(self.method_period) * (posted_count + i))

                # method_progress_factor is an annual percentage (e.g. 15 means 15%)
                # Monthly rate = factor / 12 months / 100 = 15/12/100 = 0.0125
                monthly_rate = (self.method_progress_factor or 0.0) / 12.0
                amount = self.currency_id.round(remaining_balance * monthly_rate)

                if not float_is_zero(amount, precision_rounding=self.currency_id.rounding):
                    days = (period_end_depreciation_date - period_start_date).days
                    depreciation_move_values.append(self.env['account.move']._prepare_move_for_asset_depreciation({
                        'amount': amount,
                        'asset_id': self,
                        'depreciation_beginning_date': period_start_date,
                        'date': period_end_depreciation_date,
                        'asset_number_days': days,
                    }))

                remaining_balance -= amount

            return depreciation_move_values
        
        return super()._recompute_board(start_depreciation_date=start_depreciation_date)
