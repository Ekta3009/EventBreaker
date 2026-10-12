package test;

import org.springframework.context.event.EventListener;

public class SimpleConsumer {

    private InventoryStore inventoryStore;

    @EventListener
    public void handle(InventoryUpdated event) {
        inventoryStore.save(event.getItemId());
    }
}