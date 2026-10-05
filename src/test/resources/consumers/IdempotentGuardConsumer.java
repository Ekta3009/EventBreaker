package test;

import com.eventbreaker.consumer.annotation.EventBreakerConsumer;

/**
 * Safe consumer — explicit idempotency guard.
 *
 * The very first thing this handler does is check whether the event
 * has already been processed (by its unique event ID). If it has,
 * it returns immediately without doing any work. This is the standard
 * "deduplication store" pattern that makes at-least-once delivery safe:
 * the second delivery hits the guard and exits before any side effect occurs.
 */
public class IdempotentGuardConsumer {

    private ProcessedEventStore processedEventStore;
    private InventoryRepository inventoryRepository;

    @EventBreakerConsumer(eventType = "StockReserved")
    public void handle(StockReserved event) {
        if (processedEventStore.contains(event.getEventId())) {
            return;
        }
        processedEventStore.add(event.getEventId());
        inventoryRepository.reserve(event.getItemId(), event.getQuantity());
    }
}
