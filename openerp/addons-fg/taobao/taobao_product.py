# -*- coding: utf-8 -*-
##############################################################################
#    Taobao OpenERP Connector
#    Copyright 2013 OSCG
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################

from osv import osv, fields
import decimal_precision as dp
from tools.translate import _
import tools
import time
import openerp

from .taobao_top import TOP
from taobao_base import mq_client
from taobao_base import msg_route
from taobao_base import TaobaoMixin
import logging
_logger = logging.getLogger(__name__)

class taobao_shop(osv.osv, TaobaoMixin):
    _inherit = "taobao.shop"
    _columns = {
            #taobao product
            'taobao_product_ids': fields.one2many('taobao.product', 'taobao_shop_id', u'淘宝宝贝'),
            'taobao_product_category_id': fields.many2one('product.category', u'淘宝产品分类', select=1, required=True, domain="[('type','=','normal')]"),
            'taobao_product_supplier' : fields.many2one('res.partner', 'Supplier', required=True,domain = [('supplier','=',True)], ondelete='cascade', help="Supplier of this product"),

            'taobao_product_warehouse_id': fields.many2one('stock.warehouse', 'Warehouse', required=True, ondelete="cascade"),
            'taobao_product_location_id': fields.many2one('stock.location', u'淘宝产品库位', required=True, domain="[('usage', '=', 'internal')]"),

            'taobao_product_uom': fields.many2one('product.uom', 'Product UOM', required=True),

            'taobao_product_cost_method': fields.selection([('standard','Standard Price'), ('average','Average Price')], 'Costing Method', required=True,
            help="Standard Price: the cost price is fixed and recomputed periodically (usually at the end of the year), Average Price: the cost price is recomputed at each reception of products."),
            'taobao_product_type': fields.selection([('product','Stockable Product'),('consu', 'Consumable'),('service','Service')], 'Product Type', required=True, help="Will change the way procurements are processed. Consumable are product where you don't manage stock."),
            'taobao_product_supply_method': fields.selection([('produce','Produce'),('buy','Buy')], 'Supply method', required=True, help="Produce will generate production order or tasks, according to the product type. Buy will trigger purchase orders when requested."),
            'taobao_product_procure_method': fields.selection([('make_to_stock','Make to Stock'),('make_to_order','Make to Order')], 'Procurement Method', required=True, help="'Make to Stock': When needed, take from the stock or wait until re-supplying. 'Make to Order': When needed, purchase or produce for the procurement request."),

            'taobao_product_min_qty': fields.float('Min Quantity', required=True,
            help="When the virtual stock goes below the Min Quantity specified for this field, OpenERP generates "\
            "a procurement to bring the virtual stock to the Max Quantity."),
            'taobao_product_max_qty': fields.float('Max Quantity', required=True,
            help="When the virtual stock goes below the Min Quantity, OpenERP generates "\
            "a procurement to bring the virtual stock to the Quantity specified as Max Quantity."),

            }

    _defaults = {
            }

class product_supplierinfo(osv.osv, TaobaoMixin):
    _inherit = "product.supplierinfo"


class product_template(osv.osv, TaobaoMixin):
    _inherit = "product.template"
    _columns = {
            'taobao_item_num_iid': fields.char(u'淘宝宝贝编码', size = 64),
            }

class product_product(osv.osv, TaobaoMixin):
    _inherit = "product.product"

    def _get_taobao_qty_available(self, cr, uid, ids, field_name, arg, context=None):
        res = {}
        for product in self.browse(cr, uid, ids, context=context):
            res[product.id] = product.virtual_available - product.taobao_wait_buyer_pay_qty

        return res

    def _get_taobao_wait_buyer_pay_qty(self, cr, uid, ids, field_name, arg, context=None):
        res = {}
        for product in self.browse(cr, uid, ids, context=context):
            sql_req= """
            SELECT sum(l.product_uom_qty)
            FROM sale_order_line l
            JOIN
                sale_order so ON (l.order_id = so.id)
            WHERE
                so.taobao_trade_status = 'WAIT_BUYER_PAY'
                AND l.product_id = %d
            """ % product.id
            cr.execute(sql_req)
            qty_ids = [x[0] for x in cr.fetchall()]
            res[product.id] = qty_ids[0] if qty_ids else 0

        return res

    _columns = {
            'taobao_product_ids': fields.one2many('taobao.product', 'product_product_id', u'淘宝宝贝'),
            'taobao_qty_available': fields.function(_get_taobao_qty_available, type='float', digits_compute=dp.get_precision('Product UoM'), string=u'淘宝库存', help = u'淘宝库存 = 可供数量 - 淘宝已拍未付款'),
            'taobao_wait_buyer_pay_qty': fields.function(_get_taobao_wait_buyer_pay_qty, type='float', digits_compute=dp.get_precision('Product UoM'), string=u'淘宝已拍未付款',),
            }

class taobao_product(osv.osv, TaobaoMixin):
    _name = "taobao.product"
    _description = "Taobao Sku"

    def _get_taobao_item_url(self, cr, uid, ids, field_name, arg, context=None):
        res = {}
        for product in self.browse(cr, uid, ids, context=context):
            res[product.id] = 'http://item.taobao.com/item.htm?id=%s' % product.taobao_num_iid if product.taobao_num_iid else None
        return res

    _columns = {
            'name': fields.char(u'淘宝商品名称', size=256),
            'taobao_num_iid': fields.char(u'商品数字编码', size = 64),
            'taobao_sku_id': fields.char(u'Sku id', size = 64),
            'taobao_sku_properties_name': fields.char(u'Sku属性', size = 256),
            #'taobao_item_url': fields.function(_get_taobao_item_url, type='char', string=u'宝贝地址'),
            'taobao_outer_id': fields.char(u'商家外部编码', size = 64),
            'product_product_id': fields.many2one('product.product', 'Product', select=True),
            'taobao_shop_id': fields.many2one('taobao.shop', 'Taobao Shop', select=True),
            }


    def _top_items_get(self, shop, top, search_q):
        items = []
        page_no = 0
        page_size = 50
        total_results = 999
        #一页一页的读取数据
        while(total_results > page_no*page_size):
            if search_q:
               # taobao.items.inventory.get取的是下架的商品;taobao.items.onsale.get取的是上架的商品
               rsp =top('taobao.items.onsale.get', q = search_q, nicks = shop.taobao_nick, fields = ['num_iid','title', 'pic_url', 'outer_id', 'price', 'volume'], page_no = page_no + 1, page_size = page_size)
            else:
                rsp =top('taobao.items.onsale.get', nicks = shop.taobao_nick, fields = ['num_iid','title', 'pic_url', 'outer_id', 'price', 'volume'], page_no = page_no + 1, page_size = page_size)
                
            if rsp and rsp.get('items', False): 
                items = items + rsp.Items.Item
            total_results = int(rsp.total_results)
            page_no += 1
            time.sleep(1/1000)

        page_no = 0
        page_size = 50
        total_results = 999
        while(total_results > page_no*page_size):
            if search_q:
               # taobao.items.inventory.get取的是下架的商品;taobao.items.onsale.get取的是上架的商品
               rsp2 =top('taobao.items.inventory.get', q = search_q, nicks = shop.taobao_nick, fields = ['num_iid','title', 'pic_url', 'outer_id', 'price', 'volume'], page_no = page_no + 1, page_size = page_size)
            else:
               rsp2 =top('taobao.items.inventory.get', nicks = shop.taobao_nick, fields = ['num_iid','title', 'pic_url', 'outer_id', 'price', 'volume'], page_no = page_no + 1, page_size = page_size)
                
            if rsp2 and rsp2.get('items', False): 
                items = items + rsp2.Items.Item
            total_results = int(rsp2.total_results)
            page_no += 1
            time.sleep(1/1000)

        return items


    def _top_item_skus_get(self, shop, top, num_iids):
        rsp =top('taobao.item.skus.get', num_iids = num_iids, fields = ['sku_id','num_iid','quantity'])
        if rsp and rsp.get('skus', False):
            return rsp.skus.sku
        else:
            return []

    def _top_item_quantity_update(self, top, quantity, num_iid, sku_id = None, update_type = 1):
        if sku_id:
            rsp =top('taobao.item.quantity.update', num_iid = num_iid, sku_id = sku_id, quantity = int(quantity), TYPE = update_type)
        else:
            rsp =top('taobao.item.quantity.update', num_iid = num_iid, quantity = int(quantity), TYPE = update_type)

        if rsp and rsp.get('item', False):
            return rsp['item']
        else:
            return []

    def _get_create_product(self, pool, cr, uid, shop, top, taobao_num_iid =None,  taobao_sku_id = None, taobao_product_category_id = None, taobao_product_supplier = None, taobao_product_warehouse_id = None, taobao_product_location_id = None, taobao_product_cost_method = None, taobao_product_type = None, taobao_product_supply_method = None, taobao_product_procure_method = None, taobao_product_min_qty = None, taobao_product_max_qty = None, taobao_product_uom =
            None, is_update_stock = False):

        rsp = top('taobao.item.get', num_iid = taobao_num_iid, fields=['title', 'num_iid', 'outer_id', 'num', 'price'])
        if not rsp: return
        item = rsp.get('item', None)
        if not item: return

        vals = {}
        vals['categ_id'] = taobao_product_category_id if taobao_product_category_id else shop.taobao_product_category_id.id
        vals['name'] = item.title

        vals['cost_method'] = taobao_product_cost_method if taobao_product_cost_method else shop.taobao_product_cost_method
        vals['type'] = taobao_product_type if taobao_product_type else shop.taobao_product_type
        vals['procure_method'] = taobao_product_procure_method if taobao_product_procure_method else shop.taobao_product_procure_method
        vals['supply_method'] = taobao_product_supply_method if taobao_product_supply_method else shop.taobao_product_supply_method
        vals['uom_id'] = taobao_product_uom if taobao_product_uom else shop.taobao_product_uom.id
        
        # 如果是MTS，则设置“最小库存规则”
        if vals['procure_method'] == 'make_to_stock':
            # add min order rules
            vals['orderpoint_ids'] = [[5, False, False]] + [[0, False, {
                'warehouse_id': taobao_product_warehouse_id if taobao_product_warehouse_id else shop.taobao_product_warehouse_id.id,
                'location_id': taobao_product_location_id if taobao_product_location_id else shop.taobao_product_location_id.id,
                'product_min_qty': taobao_product_min_qty if taobao_product_min_qty else shop.taobao_product_min_qty,
                'product_max_qty': taobao_product_max_qty if taobao_product_max_qty else shop.taobao_product_max_qty,
                'product_uom': taobao_product_uom if taobao_product_uom else shop.taobao_product_uom.id
                }]]

        vals['taobao_item_num_iid'] = item.num_iid
        #vals['default_code'] = item.get('outer_id', '')
        outer_id = item.get('outer_id', '')
        vals['list_price'] = float(str(item.price))
        vals['qty_available'] = float(item.num)
        vals['taobao_sku_properties_name'] = None

        if taobao_sku_id:
            rsp = top('taobao.item.sku.get', sku_id=taobao_sku_id, num_iid=taobao_num_iid,fields=['num_iid', 'quantity', 'price', 'outer_id', 'properties_name'])
            if not rsp: return
            sku = rsp.get('sku', None)
            if not sku: return

            vals['list_price'] = float(str(sku.price))
            vals['qty_available'] = float(sku.quantity)
            vals['taobao_sku_properties_name'] = sku.properties_name
            #vals['default_code'] = sku.get('outer_id', '')
            outer_id = sku.get('outer_id', '')  #item.get('outer_id', '')

        taobao_product = self._get(cr, uid, args = [('taobao_num_iid', '=', taobao_num_iid), ('taobao_sku_id', '=', taobao_sku_id)])

        if taobao_product:
            product = pool.get('product.product')._save(cr, uid, ids = taobao_product.product_product_id.id,  **vals)
        else:
            product_tmpl = pool.get('product.template')._save(cr, uid, args=[('taobao_item_num_iid','=',item.num_iid)], **vals)
            if vals['supply_method'] == 'buy' and not product_tmpl.seller_ids:
                #add sellerinfo
                pool.get('product.supplierinfo')._save(cr, uid, args=[('product_id','=',product_tmpl.id)], **{'product_id':product_tmpl.id, 'name': taobao_product_supplier if taobao_product_supplier else shop.taobao_product_supplier.id, 'min_qty': 1 })

            vals['product_tmpl_id'] = product_tmpl.id
            if vals['supply_method'] == 'buy':
                vals['default_code'] = outer_id
            product = pool.get('product.product')._save(cr, uid, **vals)
            # 如果供应方式是生产，则自动构建BoM表，将淘宝商品映射到系统中的“商家外部编码”的OE产品
            if vals['supply_method'] == 'produce' and outer_id:
                oe_product_id = pool.get('product.product').search(cr,uid,[('default_code','=',outer_id)])
                if oe_product_id:
                    oe_product = pool.get('product.product').browse(cr,uid,oe_product_id[0])
                    bom_vals = {'name': vals['name'],
                            'type': 'phantom',
                            'product_id': product.id,
                            'product_uom': vals['uom_id'],
                            'product_qty': 1,
                            'bom_lines': [(0,0,{'product_id':oe_product.id,'name':oe_product.name, 'product_uom':oe_product.uom_id.id, 'product_qty': 1})],
                }
                    pool.get('mrp.bom').create(cr,uid,bom_vals)

        taobao_product = self._save(cr, uid,
                args = [
                    ('taobao_num_iid', '=', taobao_num_iid),
                    ('taobao_sku_id', '=', taobao_sku_id)
                    ],
                **{
                    'name': item.title,
                    'taobao_num_iid': taobao_num_iid,
                    'taobao_sku_id': taobao_sku_id,
                    'taobao_sku_properties_name': vals['taobao_sku_properties_name'],
                    'taobao_outer_id': outer_id,
                    'product_product_id': product.id,  'taobao_shop_id':shop.id})


        # update stock
        if vals['procure_method'] == 'make_to_stock' and product and is_update_stock and taobao_product_location_id:
            inventry_obj = self.pool.get('stock.inventory')
            inventry_line_obj = self.pool.get('stock.inventory.line')
            inventory_id = inventry_obj.create(cr , uid, {'name': _(u'淘宝: %s') % tools.ustr(shop.taobao_nick)})
            line_data ={
                'inventory_id' : inventory_id,
                'product_qty' : vals['qty_available'] + product.taobao_wait_buyer_pay_qty - product.outgoing_qty - product.incoming_qty,
                'location_id' : taobao_product_location_id,
                'product_id' : product.id,
                'product_uom' : taobao_product_uom if taobao_product_uom else shop.taobao_product_uom.id,
            }
            inventry_line_obj.create(cr , uid, line_data)
            inventry_obj.action_confirm(cr, uid, [inventory_id])
            inventry_obj.action_done(cr, uid, [inventory_id])
        
        cr.commit()
        return product if product else None


@mq_client
@msg_route(code = 202, notify = 'notify_item', status = 'ItemAdd')
def TaobaoItemAdd(dbname, uid, app_key, rsp):
    #新增商品
    pass

@mq_client
@msg_route(code = 202, notify = 'notify_item', status = 'ItemUpshelf')
def TaobaoItemUpshelf(dbname, uid, app_key, rsp):
    #上架商品
    pass

@mq_client
@msg_route(code = 202, notify = 'notify_item', status = 'ItemDownshelf')
def TaobaoItemDownshelf(dbname, uid, app_key, rsp):
    #下架商品
    pass

@mq_client
@msg_route(code = 202, notify = 'notify_item', status = 'ItemDelete')
def TaobaoItemDelete(dbname, uid, app_key, rsp):
    #删除商品
    pass

@mq_client
@msg_route(code = 202, notify = 'notify_item', status = 'ItemUpdate')
def TaobaoItemUpdate(dbname, uid, app_key, rsp):
    #更新商品
    pass

@mq_client
@msg_route(code = 202, notify = 'notify_item', status = 'ItemRecommendDelete')
def TaobaoItemRecommendDelete(dbname, uid, app_key, rsp):
    #取消橱窗推荐商品
    pass

@mq_client
@msg_route(code = 202, notify = 'notify_item', status = 'ItemRecommendAdd')
def TaobaoItemRecommendAdd(dbname, uid, app_key, rsp):
    #橱窗推荐商品
    pass

@mq_client
@msg_route(code = 202, notify = 'notify_item', status = 'ItemZeroStock')
def TaobaoItemZeroStock(dbname, uid, app_key, rsp):
    #商品卖空
    pass

@mq_client
@msg_route(code = 202, notify = 'notify_item', status = 'ItemPunishDelete')
def TaobaoItemPunishDelete(dbname, uid, app_key, rsp):
    #小二删除商品
    pass

@mq_client
@msg_route(code = 202, notify = 'notify_item', status = 'ItemPunishDownshelf')
def TaobaoItemPunishDownshelf(dbname, uid, app_key, rsp):
    #小二下架商品
    pass

@mq_client
@msg_route(code = 202, notify = 'notify_item', status = 'ItemPunishCc')
def TaobaoItemPunishCc(dbname, uid, app_key, rsp):
    #小二cc商品
    pass

@mq_client
@msg_route(code = 202, notify = 'notify_item', status = 'ItemSkuZeroStock')
def TaobaoItemSkuZeroStock(dbname, uid, app_key, rsp):
    #商品sku卖空
    pass

@mq_client
@msg_route(code = 202, notify = 'notify_item', status = 'ItemStockChanged')
def TaobaoItemStockChanged(dbname, uid, app_key, rsp):
    #更新商品库存
    pass


@mq_client
@msg_route(code = 9999, notify = 'import_taobao_product')
def import_taobao_product(dbname, uid, app_key, rsp):
    #导入淘宝产品
    line = rsp.packet.msg.import_taobao_product
    pool = openerp.pooler.get_pool(dbname)
    cr = pool.db.cursor()
    try:
        shop = pool.get('taobao.shop')._get(cr, uid, args = [('taobao_app_key','=',app_key)])
        top = TOP(shop.taobao_app_key, shop.taobao_app_secret, shop.taobao_session_key)
        taobao_product_obj = pool.get('taobao.product')

        skus = taobao_product_obj._top_item_skus_get(shop, top, line.get("taobao_num_iid", None))
        if skus:
            for sku in skus:
                taobao_product_obj._get_create_product(pool, cr, uid, shop, top,
                        taobao_num_iid = line.get('taobao_num_iid', None),
                        taobao_sku_id = sku.sku_id,

                        taobao_product_category_id = line.get('taobao_product_category_id', None),
                        taobao_product_supplier = line.get('taobao_product_supplier', None),
                        taobao_product_warehouse_id = line.get('taobao_product_warehouse_id', None),
                        taobao_product_location_id = line.get('taobao_product_location_id', None),
                        taobao_product_cost_method = line.get('taobao_product_cost_method', None),
                        taobao_product_type = line.get('taobao_product_type', None),
                        taobao_product_supply_method = line.get('taobao_product_supply_method', None),
                        taobao_product_procure_method = line.get('taobao_product_procure_method', None),
                        taobao_product_min_qty = line.get('taobao_product_min_qty', None),
                        taobao_product_max_qty = line.get('taobao_product_max_qty', None),
                        taobao_product_uom = line.get('taobao_product_uom', None),
                        is_update_stock = line.get('is_update_stock', None),
                        )
        else:
            taobao_product_obj._get_create_product(pool, cr, uid, shop, top,
                    taobao_num_iid = line.get('taobao_num_iid', None),
                    taobao_product_category_id = line.get('taobao_product_category_id', None),
                    taobao_product_supplier = line.get('taobao_product_supplier', None),
                    taobao_product_warehouse_id = line.get('taobao_product_warehouse_id', None),
                    taobao_product_location_id = line.get('taobao_product_location_id', None),
                    taobao_product_cost_method = line.get('taobao_product_cost_method', None),
                    taobao_product_type = line.get('taobao_product_type', None),
                    taobao_product_supply_method = line.get('taobao_product_supply_method', None),
                    taobao_product_procure_method = line.get('taobao_product_procure_method', None),
                    taobao_product_min_qty = line.get('taobao_product_min_qty', None),
                    taobao_product_max_qty = line.get('taobao_product_max_qty', None),
                    taobao_product_uom = line.get('taobao_product_uom', None),
                    is_update_stock = line.get('is_update_stock', None),
                    )

    finally:
        cr.close()

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
