package com.eventbreaker.event;

public class OrderProcessed {

    private final String orderId;

    public OrderProcessed(String orderId) {
        this.orderId = orderId;
    }

    public String getOrderId() {
        return orderId;
    }
}