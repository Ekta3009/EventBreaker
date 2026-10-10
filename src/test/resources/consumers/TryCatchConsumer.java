package test;

import com.eventbreaker.consumer.annotation.EventBreakerConsumer;

/**
 * Real-world pattern — "log and continue" error handling.
 *
 * Every downstream call is wrapped in its own try-catch so that one
 * failure does not stop the rest of the handler. The consumer never
 * propagates an exception, so the broker always acks the message —
 * even when the shipment was never booked.
 */
public class TryCatchConsumer {

    private final ShipmentService shipmentService;
    private final InventoryService inventoryService;
    private final NotificationService notificationService;
    private final AuditLogger auditLogger;

    public TryCatchConsumer(
            ShipmentService shipmentService,
            InventoryService inventoryService,
            NotificationService notificationService,
            AuditLogger auditLogger) {
        this.shipmentService = shipmentService;
        this.inventoryService = inventoryService;
        this.notificationService = notificationService;
        this.auditLogger = auditLogger;
    }

    @EventBreakerConsumer(eventType = "OrderPacked")
    public void onOrderPacked(OrderPacked event) {
        try {
            inventoryService.release(event.getWarehouseId(), event.getOrderId());
        } catch (Exception e) {
            System.err.println("inventory release failed: " + e.getMessage());
        }

        try {
            shipmentService.book(event.getOrderId(), event.getAddress());
        } catch (Exception e) {
            System.err.println("shipment booking failed: " + e.getMessage());
        }

        try {
            notificationService.sendShippedEmail(event.getCustomerId());
        } catch (Exception e) {
            System.err.println("notification failed: " + e.getMessage());
        }

        try {
            auditLogger.log(event.getOrderId(), "ORDER_SHIPPED");
        } catch (Exception ignored) {
        }
    }
}
