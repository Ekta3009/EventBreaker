package test;

import com.eventbreaker.consumer.annotation.EventBreakerConsumer;

public class SimpleConsumer {

    private InventoryStore inventoryStore;

    @EventBreakerConsumer(eventType = "InventoryUpdated")
    public void handle(InventoryUpdated event) {
        inventoryStore.save(event.getItemId());
    }
}