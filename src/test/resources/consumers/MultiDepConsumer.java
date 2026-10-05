package test;

import com.eventbreaker.consumer.annotation.EventBreakerConsumer;

/**
 * Adversarial consumer — 4 constructor-injected dependencies.
 *
 * Tests that EventBreaker correctly handles constructor injection
 * when more than two dependencies are present. Each dependency is
 * called on every event delivery, so duplicate delivery produces
 * double the invocation count across all four services.
 */
public class MultiDepConsumer {

    private final OrderRepository orderRepository;
    private final InventoryService inventoryService;
    private final NotificationService notificationService;
    private final AuditLogger auditLogger;

    public MultiDepConsumer(
            OrderRepository orderRepository,
            InventoryService inventoryService,
            NotificationService notificationService,
            AuditLogger auditLogger) {
        this.orderRepository = orderRepository;
        this.inventoryService = inventoryService;
        this.notificationService = notificationService;
        this.auditLogger = auditLogger;
    }

    @EventBreakerConsumer(eventType = "OrderPlaced")
    public void handle(OrderPlaced event) {
        Order order = orderRepository.findById(event.getOrderId());
        inventoryService.reserve(event.getItemId(), event.getQuantity());
        notificationService.sendConfirmation(event.getCustomerId());
        auditLogger.log(event.getOrderId(), "ORDER_PLACED");
    }
}
