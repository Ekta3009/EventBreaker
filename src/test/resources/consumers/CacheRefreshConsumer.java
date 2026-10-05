package test;

import com.eventbreaker.consumer.annotation.EventBreakerConsumer;

/**
 * Safe consumer — idempotent cache refresh.
 *
 * A cache put with the same key is idempotent: running this handler
 * twice just overwrites the cache entry with the same value a second
 * time. No money is moved, no notification is sent, and no record
 * is duplicated — the end state is identical regardless of how many
 * times the event is delivered.
 */
public class CacheRefreshConsumer {

    private ConfigStore configStore;
    private LocalCache localCache;

    @EventBreakerConsumer(eventType = "ConfigChanged")
    public void handle(ConfigChanged event) {
        String latestValue = configStore.get(event.getConfigKey());
        localCache.put(event.getConfigKey(), latestValue);
    }
}
