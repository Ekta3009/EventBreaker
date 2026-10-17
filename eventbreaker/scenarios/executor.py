import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from eventbreaker.classpath.resolver import project_root
from eventbreaker.models.consumer import ConsumerAnalysis, DependencyInfo
from eventbreaker.models.observation import ObservationEntry, ObservationResult
from eventbreaker.sandbox.executor import SandboxExecutor
from eventbreaker.scenarios.models import ChaosScenario, TestConfig


# ── Temp Maven project pom.xml ────────────────────────────────────────────────
#
# No JUnit. Mockito is compile-scoped so it ends up in the fat JAR.
# maven-shade-plugin bundles everything into a single executable JAR.
# ServicesResourceTransformer preserves Mockito's META-INF/services files
# so the mock maker is found correctly at runtime.

_POM = """\
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
             http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>
    <groupId>eventbreaker.generated</groupId>
    <artifactId>scenario-execution</artifactId>
    <version>1.0.0</version>

    <properties>
        <maven.compiler.source>21</maven.compiler.source>
        <maven.compiler.target>21</maven.compiler.target>
        <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
    </properties>

    <dependencies>
        <dependency>
            <groupId>org.mockito</groupId>
            <artifactId>mockito-core</artifactId>
            <version>5.14.2</version>
        </dependency>
        <dependency>
            <groupId>com.fasterxml.jackson.core</groupId>
            <artifactId>jackson-databind</artifactId>
            <version>2.17.1</version>
        </dependency>
CLASSPATH_DEPENDENCIES
    </dependencies>

    <build>
        <plugins>
            <plugin>
                <groupId>org.apache.maven.plugins</groupId>
                <artifactId>maven-shade-plugin</artifactId>
                <version>3.5.2</version>
                <executions>
                    <execution>
                        <phase>package</phase>
                        <goals><goal>shade</goal></goals>
                        <configuration>
                            <createDependencyReducedPom>false</createDependencyReducedPom>
                            <finalName>eventbreaker-execution</finalName>
                            <transformers>
                                <transformer implementation="org.apache.maven.plugins.shade.resource.ManifestResourceTransformer">
                                    <mainClass>eventbreaker.generated.EventBreakerExecution</mainClass>
                                </transformer>
                                <transformer implementation="org.apache.maven.plugins.shade.resource.ServicesResourceTransformer"/>
                            </transformers>
                        </configuration>
                    </execution>
                </executions>
            </plugin>
        </plugins>
    </build>
</project>
"""

# ── Generated execution class template ───────────────────────────────────────
#
# Single unified Java main class — no test framework.
# Uses plain string replacement (UPPER_CASE markers) to avoid
# escaping conflicts between Python f-strings and Java braces.
# Mockito is used purely as an invocation recorder.
# FAULT_INJECTIONS and CALL_PATTERN carry the scenario-specific code.

_EXECUTION_TEMPLATE = """\
package eventbreaker.generated;

import org.mockito.Mockito;
import org.mockito.invocation.Invocation;
import static org.mockito.Mockito.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;

EXTRA_IMPORTS

/**
 * EventBreaker generated execution harness.
 * Scenario: SCENARIO_TYPE
 *
 * Mocks all consumer dependencies as recorders, runs the consumer
 * under a controlled scenario, and reports invocation counts as JSON.
 */
public class EventBreakerExecution {

    public static void main(String[] args) {
        try {
            run();
        } catch (Throwable e) {  // Errors too: a missing class is a NoClassDefFoundError
            StackTraceElement[] at = e.getStackTrace();
            System.out.println("EVENTBREAKER_ERROR:" + e
                + (at.length > 0 ? " at " + at[0] : ""));
            System.exit(1);
        }
    }

    private static void run() throws Exception {
CAST_TYPES

        // ── Instrumented dependencies (recorders, not simulators) ──────────
        //
        // Each dependency is replaced with a Mockito recorder that counts
        // every invocation, tracks call ordering, and whether each call threw.
        List<String> callSequence = Collections.synchronizedList(new ArrayList<>());
MOCK_DECLARATIONS

        // ── Instantiate the real consumer with recorder dependencies ───────
CONSUMER_INSTANTIATION

        // ── Create the event ───────────────────────────────────────────────
        // Numeric getters return 1 and boolean getters return true, so guard
        // clauses like `if (event.getAmount() <= 0) return;` don't short-circuit.
        EVENT_CLASS event = (EVENT_CLASS) mock(eventbreaker.generated.__EBInstantiate.typeFor(EVENT_CLASS.class, "event"),
            eventbreaker.generated.__EBInstantiate.settings(EVENT_CLASS.class, "event"));

        // ── Reset counters — exclude stubbing-phase invocations ────────────
        // when(...).thenReturn(...) triggers one invocation on the stub target.
        // Clear Mockito log, threwCounts, and callSequence so only scenario-time
        // calls appear in the final report.
RESET_INVOCATIONS

        // ── Fault injections ───────────────────────────────────────────────
FAULT_INJECTIONS

        // ── Plain listener parameters (read after the path setup's "param.*" overrides) ──
VALUE_PARAMS

        // ── Project static helpers: real, fake answer only if the app isn't running ──
STATIC_HELPERS

        // doThrow/doAnswer setup above fires invocation listeners — clear so
        // callSequence only contains actual scenario-time calls.
        callSequence.clear();

        // ── Execute scenario ───────────────────────────────────────────────
        AtomicBoolean consumerThrew = new AtomicBoolean(false);
CALL_PATTERN

        // ── Collect invocation counts from every recorder ──────────────────
        Map<String, Object> __ebDeps = new LinkedHashMap<>();
DEP_MAP_ENTRIES

        ObjectMapper __ebMapper = new ObjectMapper();
        ArrayNode __ebObservations = __ebMapper.createArrayNode();

        for (Map.Entry<String, Object> __ebEntry : __ebDeps.entrySet()) {
            Collection<Invocation> __ebInvocations =
                Mockito.mockingDetails(__ebEntry.getValue()).getInvocations();
            Map<String, Integer> __ebCounts = new LinkedHashMap<>();
            for (Invocation __ebInv : __ebInvocations) {
                __ebCounts.merge(__ebInv.getMethod().getName(), 1, Integer::sum);
            }
            for (Map.Entry<String, Integer> __ebMc : __ebCounts.entrySet()) {
                ObjectNode __ebObs = __ebMapper.createObjectNode();
                __ebObs.put("target", __ebEntry.getKey() + "." + __ebMc.getKey());
                __ebObs.put("callCount", __ebMc.getValue());
                __ebObs.put("threw", threwCounts.getOrDefault(__ebEntry.getKey() + "." + __ebMc.getKey(), 0) > 0);
                __ebObservations.add(__ebObs);
            }
        }

        ObjectNode __ebResult = __ebMapper.createObjectNode();
        __ebResult.put("scenario", "SCENARIO_TYPE");
        __ebResult.put("consumerThrew", consumerThrew.get());
        ArrayNode __ebSeq = __ebMapper.createArrayNode();
        callSequence.forEach(__ebSeq::add);
        __ebResult.set("callSequence", __ebSeq);
        __ebResult.set("observations", __ebObservations);

        System.out.println(
            "EVENTBREAKER_RESULT:" + __ebMapper.writeValueAsString(__ebResult)
        );
    }
}
"""


# ─────────────────────────────────────────────────────────────────────────────

_INSTANTIATE_HELPER = """\
package eventbreaker.generated;

import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Modifier;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;
import org.mockito.Mockito;
import org.mockito.invocation.InvocationOnMock;
import org.mockito.stubbing.Answer;
import static org.mockito.Mockito.*;

/**
 * Real-classpath support: builds the consumer without knowing its constructor at
 * generation time, and drives every fake through one name-based answer.
 *
 * Fakes are addressed by path: "dep.method" for a dependency call, and
 * "dep.getA.getB" / "event.getA.getB" for objects returned from them.
 * FAULTS and RETURNS are filled by the scenario before the consumer runs.
 */
public final class __EBInstantiate {

    /** path -> THROW | THROW_ONCE | DELAY:<ms> */
    public static final Map<String, String> FAULTS = new ConcurrentHashMap<>();
    /** path -> spec string (true, 3, STRING:x, ENUM:X, NULL) or a Class to mock */
    public static final Map<String, Object> RETURNS = new ConcurrentHashMap<>();
    private static final Map<String, AtomicInteger> CALLS = new ConcurrentHashMap<>();

    private __EBInstantiate() {}

    /** Calls the constructor with the most parameters, passing a fake (or default) for each. */
    public static <T> T create(Class<T> cls, String... names) throws Exception {
        Constructor<?> best = null;
        for (Constructor<?> c : cls.getDeclaredConstructors()) {
            if (best == null || c.getParameterCount() > best.getParameterCount()) best = c;
        }
        best.setAccessible(true);
        Class<?>[] types = best.getParameterTypes();
        Object[] args = new Object[types.length];
        // Named after the source's parameter names when they line up, so path setup can
        // override what a constructor-only fake returns (e.g. rabbitTemplate.getMessageConverter…).
        boolean named = names.length == types.length;
        java.lang.reflect.Type[] generic = best.getGenericParameterTypes();
        for (int i = 0; i < types.length; i++) {
            args[i] = valueFor(types[i], named ? names[i] : "ctor" + i);
            if (generic.length == types.length) declare(args[i], generic[i]);
        }
        return cls.cast(best.newInstance(args));
    }

    /**
     * Calls the listener method by name — works for package-private classes and methods,
     * which generated code in another package cannot reference directly.
     */
    public static void call(Object target, String method, Object... args) throws Exception {
        // Static fakes only apply on the thread that opens them: open them around every call.
        List<org.mockito.MockedStatic<?>> statics = openStatics();
        try {
            callListener(target, method, args);
        } finally {
            for (org.mockito.MockedStatic<?> st : statics) st.close();
        }
    }

    /**
     * The project's own classes whose static methods the listener calls (e.g. a tenant/security
     * helper or a singleton holder). They run for real; only when one fails because no
     * application is running (IllegalStateException "not initialized", NullPointerException)
     * does it answer like a fake. Path setup can override them as "<SimpleName>.<method>".
     */
    public static final Set<String> STATICS = ConcurrentHashMap.newKeySet();

    private static List<org.mockito.MockedStatic<?>> openStatics() {
        List<org.mockito.MockedStatic<?>> opened = new ArrayList<>();
        for (String name : STATICS) {
            try {
                Class<?> cls = load(name);
                if (cls.isEnum() || cls.isInterface() || cls.isAnnotation()) continue;
                String simple = cls.getSimpleName();
                opened.add(Mockito.mockStatic(cls, inv -> {
                    String path = simple + "." + inv.getMethod().getName();
                    if (RETURNS.containsKey(path) || FAULTS.containsKey(path)) return answer(inv, path);
                    try {
                        return inv.callRealMethod();
                    } catch (IllegalStateException | NullPointerException e) {
                        return answer(inv, path);
                    }
                }));
            } catch (Throwable e) {
                // not loadable or not fakeable: leave it real, but say so in the run's output
                System.err.println("EB_STATIC_SKIPPED " + name + ": " + e);
            }
        }
        return opened;
    }

    /** Class.forName that also finds nested classes written in source form (Action.ActionStatusCreate). */
    private static Class<?> load(String name) throws ClassNotFoundException {
        String n = name;
        while (true) {
            try {
                return Class.forName(n);
            } catch (ClassNotFoundException e) {
                int dot = n.lastIndexOf('.');
                if (dot < 0) throw e;
                n = n.substring(0, dot) + "$" + n.substring(dot + 1);
            }
        }
    }

    private static void callListener(Object target, String method, Object... args) throws Exception {
        for (Class<?> c = target.getClass(); c != null; c = c.getSuperclass()) {
            for (java.lang.reflect.Method m : c.getDeclaredMethods()) {
                if (m.getName().equals(method) && m.getParameterCount() == args.length) {
                    m.setAccessible(true);
                    try {
                        m.invoke(target, args);
                        return;
                    } catch (java.lang.reflect.InvocationTargetException e) {
                        Throwable cause = e.getCause();
                        if (cause instanceof Exception ex) throw ex;
                        throw new RuntimeException(cause);
                    }
                }
            }
        }
        throw new NoSuchMethodException(method + " with " + args.length + " argument(s)");
    }

    /** Replaces an instance field with the recorder mock; static fields (loggers, constants) are left alone. */
    public static void inject(Object target, String field, Object value) throws Exception {
        for (Class<?> c = target.getClass(); c != null; c = c.getSuperclass()) {
            try {
                Field f = c.getDeclaredField(field);
                if (Modifier.isStatic(f.getModifiers())) return;
                f.setAccessible(true);
                f.set(target, value);
                declare(value, f.getGenericType());
                return;
            } catch (NoSuchFieldException ignored) {
                // keep walking up the hierarchy
            }
        }
    }

    /**
     * Mock settings for a fake of type t at this path. Interfaces are faked without
     * their annotations (copying them can need classes referenced only from annotation
     * values, which the sandbox JAR leaves out); the inline mock maker rejects that
     * setting for classes, which it instruments in place instead of copying.
     */
    public static org.mockito.MockSettings settings(Class<?> t, String path) {
        org.mockito.MockSettings s = withSettings().defaultAnswer(answerFor(path));
        Class<?> actual = typeFor(t, path);
        List<Class<?>> extra = new ArrayList<>();
        for (Class<?> c : casts(t, path)) {
            if (c.isInterface() && !c.isAssignableFrom(actual)) extra.add(c);
        }
        if (!extra.isEmpty()) s = s.extraInterfaces(extra.toArray(new Class<?>[0]));
        return actual.isInterface() ? s.withoutAnnotations() : s;
    }

    /**
     * Root name ("event", a dependency field, a constructor argument) -> the types the listener's
     * source casts it to, e.g. (InvoiceCreationInternalEvent) event. Filled before any fake exists.
     */
    public static final Map<String, List<String>> CASTS = new ConcurrentHashMap<>();

    /** Cast targets for this root that are real subtypes of its declared type (final classes too: inline mock maker). */
    private static List<Class<?>> casts(Class<?> t, String path) {
        List<Class<?>> out = new ArrayList<>();
        for (String name : CASTS.getOrDefault(path, List.of())) {
            try {
                Class<?> c = load(name);
                if (c != t && t.isAssignableFrom(c)) out.add(c);
            } catch (Throwable ignored) {
                // a nested or unresolvable name: no extra type
            }
        }
        return out;
    }

    /**
     * The class to fake for a root: the first concrete class it is cast to, else its declared type.
     * Interfaces it is cast to are added by settings(), so every cast in the source succeeds.
     */
    public static Class<?> typeFor(Class<?> t, String path) {
        for (Class<?> c : casts(t, path)) if (!c.isInterface()) return c;
        return t;
    }

    /**
     * Declared generic type of a fake, e.g. OSGIServiceRegistration<PaymentControlPluginApi>
     * for a field, constructor parameter or return value. Java erases the type argument at
     * runtime, so without it registry.getServiceForName(..) (returns T) would answer a plain
     * Object fake and the caller's assignment would fail.
     */
    private static final Map<Object, java.lang.reflect.Type> DECLARED =
            java.util.Collections.synchronizedMap(new java.util.IdentityHashMap<>());

    private static void declare(Object fake, java.lang.reflect.Type type) {
        if (fake != null && type instanceof java.lang.reflect.ParameterizedType
                && Mockito.mockingDetails(fake).isMock()) {
            DECLARED.put(fake, type);
        }
    }

    /** Type variables of the faked type, resolved from its declared generic type when known. */
    private static org.mockito.internal.util.reflection.GenericMetadataSupport generics(InvocationOnMock inv) {
        java.lang.reflect.Type declared = DECLARED.get(inv.getMock());
        if (declared != null) {
            return org.mockito.internal.util.reflection.GenericMetadataSupport.inferFrom(declared);
        }
        Class<?> mocked = Mockito.mockingDetails(inv.getMock()).getMockCreationSettings().getTypeToMock();
        return org.mockito.internal.util.reflection.GenericMetadataSupport.inferFrom(mocked);
    }

    /** The default answer for the fake reachable at this path. */
    public static Answer<Object> answerFor(String path) {
        return inv -> answer(inv, path + "." + inv.getMethod().getName());
    }

    private static Object answer(InvocationOnMock inv, String path) throws Throwable {
        String fault = FAULTS.get(path);
        if (fault != null) {
            int n = CALLS.computeIfAbsent(path, k -> new AtomicInteger()).incrementAndGet();
            if (fault.equals("THROW") || (fault.equals("THROW_ONCE") && n == 1)) {
                throw failure(inv.getMethod(), "EventBreaker: " + fault + " on " + path);
            }
            if (fault.startsWith("DELAY:")) {
                Thread.sleep(Long.parseLong(fault.substring(6)));
            }
        }
        java.lang.reflect.Method m = inv.getMethod();
        Class<?> rt = returnType(inv);
        if (rt == void.class) return null;
        if (RETURNS.containsKey(path)) {
            Object spec = RETURNS.get(path);
            if (spec instanceof String list && list.startsWith("LIST:")) {
                return listOf(convert(list.substring(5), elementType(inv), path), rt);
            }
            return convert(spec, rt, path);
        }
        // <S extends T> S save(S entity): the method returns what it was given (JPA save /
        // saveAndFlush / merge, fluent builders). An Object fake would fail the caller's cast.
        java.lang.reflect.Type grt = m.getGenericReturnType();
        if (grt instanceof java.lang.reflect.TypeVariable<?>) {
            java.lang.reflect.Type[] params = m.getGenericParameterTypes();
            for (int i = 0; i < params.length; i++) {
                if (params[i].equals(grt)) return inv.getArgument(i);
            }
        }
        if (rt == boolean.class || rt == Boolean.class) return true;
        if (rt == int.class || rt == Integer.class) return 1;
        if (rt == long.class || rt == Long.class) return 1L;
        if (rt == double.class || rt == Double.class) return 1.0;
        if (rt == float.class || rt == Float.class) return 1.0f;
        if (rt == short.class || rt == Short.class) return (short) 1;
        if (rt == byte.class || rt == Byte.class) return (byte) 1;
        if (rt.isArray()) {   // one element; a primitive array (byte[] message body) holds a zero
            Object arr = java.lang.reflect.Array.newInstance(rt.getComponentType(), 1);
            if (!rt.getComponentType().isPrimitive()) {
                java.lang.reflect.Array.set(arr, 0, valueFor(rt.getComponentType(), path));
            }
            return arr;
        }
        if (rt == java.util.List.class || rt == java.util.Collection.class
                || rt == Iterable.class || rt == java.util.Set.class) {
            Object element = valueFor(elementType(inv), path);
            java.util.List<Object> one = new java.util.ArrayList<>();
            if (element != null) one.add(element);
            return rt == java.util.Set.class ? new java.util.HashSet<>(one) : one;
        }
        if (rt == java.util.Optional.class) {
            return java.util.Optional.ofNullable(valueFor(elementType(inv), path));
        }
        Object value = valueFor(rt, path);
        declare(value, m.getGenericReturnType());   // e.g. getRegistry() returning Registry<PluginApi>
        return value;
    }

    /**
     * The exception an injected failure throws: the method's first declared checked exception
     * (e.g. JsonProcessingException, EventBusException), so the listener's own catch block for it
     * is what gets tested; a RuntimeException when it declares none or none can be built.
     */
    private static Throwable failure(java.lang.reflect.Method m, String message) {
        for (Class<?> ex : m.getExceptionTypes()) {
            if (RuntimeException.class.isAssignableFrom(ex) || Error.class.isAssignableFrom(ex)) continue;
            Throwable t = newThrowable(ex, message);
            if (t != null) return t;
        }
        return new RuntimeException(message);
    }

    /** Builds ex with a message constructor (any visibility), else a no-arg one, else a fake of it. */
    private static Throwable newThrowable(Class<?> ex, String message) {
        if (!java.lang.reflect.Modifier.isAbstract(ex.getModifiers())) {
            for (Class<?>[] sig : new Class<?>[][] {{String.class}, {String.class, Throwable.class}, {}}) {
                try {
                    Constructor<?> c = ex.getDeclaredConstructor(sig);
                    c.setAccessible(true);
                    Object[] args = sig.length == 0 ? new Object[0]
                            : sig.length == 1 ? new Object[] {message} : new Object[] {message, null};
                    return (Throwable) c.newInstance(args);
                } catch (ReflectiveOperationException | RuntimeException ignored) {
                    // try the next constructor
                }
            }
        }
        try {   // abstract, or no usable constructor: a fake that still has the type the catch block expects
            Throwable fake = (Throwable) mock(ex, withSettings().defaultAnswer(CALLS_REAL_METHODS));
            return fake;
        } catch (RuntimeException e) {
            return null;
        }
    }

    /**
     * The most specific return type of the invoked method on the faked type.
     * Mockito may report an inherited / bridge declaration (a covariant override in a
     * sub-interface, or a generic JpaRepository method) whose erased type is just Object;
     * returning an Object fake there fails the consumer's cast.
     */
    private static Class<?> returnType(InvocationOnMock inv) {
        java.lang.reflect.Method m = inv.getMethod();
        Class<?> best = m.getReturnType();
        for (java.lang.reflect.Method c : inv.getMock().getClass().getMethods()) {
            if (c.getName().equals(m.getName())
                    && java.util.Arrays.equals(c.getParameterTypes(), m.getParameterTypes())
                    && best.isAssignableFrom(c.getReturnType())) {
                best = c.getReturnType();
            }
        }
        // A type-variable return (bounded or not) is resolved against the faked type:
        // JpaRepository<AppUser, Long>.getById(ID) -> AppUser; an entity extending
        // AbstractPersistable<Long> has getId() declared as <ID extends Serializable> -> Long.
        if (m.getGenericReturnType() instanceof java.lang.reflect.TypeVariable<?>) {
            try {
                Class<?> resolved = generics(inv).resolveGenericReturnType(m).rawType();
                if (resolved != null && best.isAssignableFrom(resolved)) best = resolved;
            } catch (RuntimeException ignored) {
                // keep Object
            }
        }
        return best;
    }

    /** A one-element collection of the return type's kind (Set, else List) holding value. */
    private static Object listOf(Object value, Class<?> rt) {
        java.util.List<Object> one = new java.util.ArrayList<>();
        one.add(value);
        return java.util.Set.class.isAssignableFrom(rt) ? new java.util.HashSet<>(one) : one;
    }

    private static Object convert(Object spec, Class<?> rt, String path) {
        if (spec instanceof Class<?> c) return valueFor(c, path);
        String s = (String) spec;
        if (s.equals("NULL")) return null;
        if (s.startsWith("STRING:")) {   // a byte[] (e.g. a message body) gets the text's bytes
            String text = s.substring(7);
            return rt == byte[].class ? text.getBytes(java.nio.charset.StandardCharsets.UTF_8) : text;
        }
        if (s.startsWith("ENUM:")) {
            String name = s.substring(5);
            for (Object c : rt.getEnumConstants()) if (((Enum<?>) c).name().equals(name)) return c;
            throw new IllegalArgumentException("No constant " + name + " in " + rt.getName());
        }
        if (rt == boolean.class || rt == Boolean.class) return Boolean.parseBoolean(s);
        if (rt == int.class || rt == Integer.class) return Integer.parseInt(s);
        if (rt == long.class || rt == Long.class) return Long.parseLong(s);
        if (rt == double.class || rt == Double.class) return Double.parseDouble(s);
        if (rt == java.math.BigDecimal.class) return new java.math.BigDecimal(s);
        return s;
    }

    /**
     * Element type of a List/Set/Optional return, with type variables resolved against
     * the faked type — e.g. CalendarRepository extends JpaRepository<Calendar, Long>:
     * findById(...) returns Optional<T> with T = Calendar.
     */
    private static Class<?> elementType(InvocationOnMock inv) {
        try {
            var ret = generics(inv).resolveGenericReturnType(inv.getMethod());
            for (java.lang.reflect.Type t : ret.actualTypeArguments().values()) {
                if (t instanceof Class<?> c && c != Object.class) return c;
                if (t instanceof java.lang.reflect.ParameterizedType pt) return (Class<?>) pt.getRawType();
            }
        } catch (RuntimeException ignored) {
            // fall back to the declared generic type
        }
        return elementType(inv.getMethod().getGenericReturnType());
    }

    private static Class<?> elementType(java.lang.reflect.Type generic) {
        if (generic instanceof java.lang.reflect.ParameterizedType pt) {
            java.lang.reflect.Type arg = pt.getActualTypeArguments()[0];
            if (arg instanceof Class<?> c) return c;
            if (arg instanceof java.lang.reflect.ParameterizedType p2) return (Class<?>) p2.getRawType();
        }
        return Object.class;
    }

    /**
     * Value for a plain listener parameter (String header, id, flag…): the path setup's
     * "param.<name>" override if any, else the same defaults fakes return.
     */
    public static Object param(String name, Class<?> t) {
        String path = "param." + name;
        Class<?> boxed = t == boolean.class ? Boolean.class : t == int.class ? Integer.class
                : t == long.class ? Long.class : t == double.class ? Double.class : t;
        if (RETURNS.containsKey(path)) return convert(RETURNS.get(path), boxed, path);
        if (boxed == Boolean.class) return true;
        if (boxed == Integer.class) return 1;
        if (boxed == Long.class) return 1L;
        if (boxed == Double.class) return 1d;
        if (t == java.math.BigDecimal.class) return java.math.BigDecimal.ONE;
        return valueFor(t, path);
    }

    private static Object valueFor(Class<?> t, String path) {
        if (t == boolean.class) return false;
        if (t == char.class) return (char) 0;
        if (t == byte.class) return (byte) 0;
        if (t == short.class) return (short) 0;
        if (t == int.class) return 0;
        if (t == long.class) return 0L;
        if (t == float.class) return 0f;
        if (t == double.class) return 0d;
        if (t == String.class) return "";
        if (t == java.util.UUID.class) return java.util.UUID.nameUUIDFromBytes(path.getBytes());
        if (t.isEnum()) {
            Object[] constants = t.getEnumConstants();
            return constants.length > 0 ? constants[0] : null;
        }
        try {
            return mock(typeFor(t, path), settings(t, path));
        } catch (RuntimeException e) {
            return null;  // final / unmockable type
        }
    }
}
"""


# javax packages that ship with the JDK. Others (javax.annotation.Nullable from jsr305,
# javax.inject, javax.persistence…) are libraries and must be on the classpath.
_JDK_JAVAX = tuple(f"javax.{p}." for p in (
    "accessibility", "annotation.processing", "crypto", "imageio", "lang.model", "management", "naming",
    "net", "print", "rmi", "script", "security", "smartcardio", "sound", "sql", "swing", "tools",
    "transaction.xa", "xml.catalog", "xml.crypto", "xml.datatype", "xml.namespace", "xml.parsers",
    "xml.stream", "xml.transform", "xml.validation", "xml.xpath",
)) + ("javax.xml.XMLConstants",)  # not javax.xml.bind / javax.xml.ws: removed from the JDK in Java 11


def _import_available(import_line: str, available: set[str]) -> bool:
    """True if an import resolves to a JDK type or a class/package on the classpath."""
    m = re.match(r"import\s+(static\s+)?([\w.]+?)(\.\*)?\s*;", import_line.strip())
    if not m:
        return True
    name = m.group(2)
    if name.startswith(("java.", "jdk.")) or name.startswith(_JDK_JAVAX):
        return True
    parts = name.split(".")
    if m.group(3):  # wildcard: the package (or class, for nested types) must exist
        return "/".join(parts) in available
    # Class (or a static member / nested type of one): try successively shorter prefixes,
    # but only ones naming a type — a bare package match (javax/annotation exists, its
    # Nullable does not) is not enough.
    for i in range(len(parts), 0, -1):
        if parts[i - 1][:1].isupper() and "/".join(parts[:i]) in available:
            return True
    return False


def _class_index(classpath: list[Path]) -> set[str]:
    """Every class ("a/b/C") and package ("a/b") available on the classpath."""
    index: set[str] = set()

    def add(name: str) -> None:
        cls = name[:-6].replace("$", "/")
        index.add(cls)
        index.add(cls.rsplit("/", 1)[0])

    for entry in classpath:
        if entry.is_dir():  # the project's own compiled classes (target/classes, build/classes/…)
            for f in entry.rglob("*.class"):
                add(f.relative_to(entry).as_posix())
            continue
        if not entry.is_file():
            continue
        try:
            with zipfile.ZipFile(entry) as z:
                for n in z.namelist():
                    if n.endswith(".class"):
                        add(n)
        except zipfile.BadZipFile:
            continue
    return index


def _classpath_contains(classpath: list[Path], entry_name: str) -> bool:
    for jar in classpath:
        if jar.is_file():
            try:
                with zipfile.ZipFile(jar) as z:
                    if entry_name in z.namelist():
                        return True
            except zipfile.BadZipFile:
                continue
    return False


def _as_jar(entry: Path) -> Path:
    """A classpath directory packed into a cached jar (rebuilt when its contents change)."""
    if not entry.is_dir():
        return entry
    files = sorted(f for f in entry.rglob("*") if f.is_file())
    stamp = hashlib.sha256(
        "\0".join(f"{f.relative_to(entry)}:{f.stat().st_mtime_ns}" for f in files).encode()
    ).hexdigest()[:12]
    key = hashlib.sha256(str(entry.resolve()).encode()).hexdigest()[:12]
    out = Path.home() / ".eventbreaker" / "cache" / "classpath-jars" / f"{key}-{stamp}.jar"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as jar:
            for f in files:
                jar.write(f, f.relative_to(entry).as_posix())
        tmp.rename(out)
    return out


# Bump when _sandbox_jar's selection logic changes, so cached jars are rebuilt.
_SANDBOX_JAR_VERSION = 2


def _sandbox_jar(fat_jar: Path, classpath: list[Path], roots: tuple[str, ...] = ()) -> Path:
    """Fat JAR + the classpath classes reachable from it (static closure via jdeps).

    Reflection-only references are not seen by jdeps; the consumer's real code
    paths are, because they are ordinary bytecode references.
    """
    # Name carries everything that changes the contents, so a cached jar is never stale.
    key = hashlib.sha256(
        f"{_SANDBOX_JAR_VERSION}\0{fat_jar.stat().st_mtime_ns}\0{','.join(roots)}".encode()
    ).hexdigest()[:12]
    out = fat_jar.with_name(f"eventbreaker-sandbox-{key}.jar")
    if out.exists():
        return out

    # Classes loaded only by name are not reachable from the fat JAR's bytecode, so
    # copy their class files (plus nested classes) next to it as extra jdeps targets.
    roots_dir = Path(tempfile.mkdtemp(prefix="eb-roots-"))
    prefixes = tuple(r.replace(".", "/") for r in roots)
    for jar in classpath:
        if not prefixes or not jar.is_file():
            continue
        with zipfile.ZipFile(jar) as src:
            for name in src.namelist():
                if name.endswith(".class") and any(
                    name == f"{pre}.class" or name.startswith(f"{pre}$") for pre in prefixes
                ):
                    target = roots_dir / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(src.read(name))

    scan = subprocess.run(
        ["jdeps", "-verbose:class", "-R", "--multi-release", "21", "--ignore-missing-deps",
         "-cp", os.pathsep.join(map(str, classpath)), str(fat_jar), str(roots_dir)],
        capture_output=True, text=True,
    )
    shutil.rmtree(roots_dir, ignore_errors=True)
    edges: dict[str, set[str]] = {}
    for line in scan.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1] == "->" and line.startswith("   "):
            edges.setdefault(parts[0], set()).add(parts[2])

    with zipfile.ZipFile(fat_jar) as fat:
        bundled = {
            n[:-6].replace("/", ".") for n in fat.namelist() if n.endswith(".class")
        }

    # Walk from everything already in the fat JAR — plus classes the harness only
    # loads by name (the consumer, via Class.forName) — to the classes they need.
    needed: set[str] = {r for r in roots if r not in bundled}
    frontier = list(bundled) + list(needed)
    while frontier:
        cls = frontier.pop()
        for dep in edges.get(cls, ()):
            if dep not in bundled and dep not in needed:
                needed.add(dep)
                frontier.append(dep)

    wanted = {c.replace(".", "/") + ".class" for c in needed}
    with zipfile.ZipFile(fat_jar) as fat, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in fat.infolist():
            dst.writestr(item, fat.read(item.filename))
        written = set(fat.namelist())
        for jar in classpath:
            if not jar.is_file():
                continue
            with zipfile.ZipFile(jar) as src:
                for name in src.namelist():
                    if name in wanted and name not in written:
                        dst.writestr(name, src.read(name))
                        written.add(name)
    return out


def _classpath_dependencies(classpath: list[Path]) -> str:
    """POM <dependency> entries for user-supplied classpath jars (system scope).

    System scope = on the compile classpath, never shaded into the fat JAR.
    The jars are put on the runtime classpath next to the JAR instead (see _run).
    """
    return "".join(
        f"""        <dependency>
            <groupId>eventbreaker.classpath</groupId>
            <artifactId>cp{i}</artifactId>
            <version>0</version>
            <scope>system</scope>
            <systemPath>{jar.resolve()}</systemPath>
        </dependency>
"""
        for i, jar in enumerate(classpath)
    )


class ScenarioExecutor:
    """Builds and runs one scenario harness per call.

    classpath: the consumer project's compiled classes and dependency jars; the
    harness compiles and runs against the consumer's real types.
    """

    def __init__(self, classpath: list[Path]) -> None:
        if not classpath:
            raise ValueError("ScenarioExecutor needs the consumer project's classpath")
        # Directories (e.g. Gradle build/classes of sibling modules) become jars:
        # Maven system-scope dependencies and the sandbox bundler both need files.
        self.classpath = [_as_jar(Path(p)) for p in classpath]
        self._classes: set[str] = _class_index(self.classpath)

    def execute(
        self,
        scenario: ChaosScenario,
        analysis: ConsumerAnalysis,
        consumer_file: Path,
    ) -> ObservationResult:
        # Use a stable cache directory keyed by a hash of the generated code.
        # Different scenarios (different testConfig) → different cache keys.
        execution_code = _generate_execution(
            analysis, consumer_file, scenario.testConfig, scenario.scenarioType,
            available_classes=self._classes,
        )
        cache_key = hashlib.sha256(
            (
                execution_code
                + consumer_file.read_text(errors="replace")
                + "\0".join(str(p) for p in self.classpath)
                # Support code compiled into the harness: a change must rebuild cached jars.
                + _INSTANTIATE_HELPER
            ).encode()
        ).hexdigest()[:16]
        cache_dir = Path.home() / ".eventbreaker" / "cache" / cache_key
        project_dir = cache_dir / "scenario"

        freshly_created = not (project_dir / "pom.xml").exists()
        if freshly_created:
            self._create_project(project_dir, consumer_file)

        # Always overwrite the execution class and helper in case analysis changed
        self._write_execution(
            project_dir, analysis, consumer_file, scenario.testConfig, scenario.scenarioType
        )
        self._generate_support(project_dir, analysis, consumer_file)

        result = self._run(
            project_dir, scenario, analysis, consumer_file, cached=_jar_exists(project_dir)
        )

        # Stale cache recovery: if the build failed on an inherited project dir,
        # it may be left over from an older EventBreaker version. Delete and retry once.
        if result.status == "ERROR" and not freshly_created and not _jar_exists(project_dir):
            shutil.rmtree(project_dir, ignore_errors=True)
            self._create_project(project_dir, consumer_file)
            self._write_execution(
                project_dir, analysis, consumer_file, scenario.testConfig, scenario.scenarioType
            )
            self._generate_support(project_dir, analysis, consumer_file)
            result = self._run(project_dir, scenario, analysis, consumer_file, cached=False)

        return result

    # ── Project setup ─────────────────────────────────────────────────────────

    def _generate_support(
        self, project_dir: Path, analysis: ConsumerAnalysis, consumer_file: Path
    ) -> None:
        # Every type the consumer uses is on the classpath: only the reflective
        # instantiation helper is needed.
        gen = project_dir / "src" / "main" / "java" / "eventbreaker" / "generated"
        gen.mkdir(parents=True, exist_ok=True)
        (gen / "__EBInstantiate.java").write_text(_INSTANTIATE_HELPER)

    def _create_project(self, project_dir: Path, consumer_file: Path) -> None:
        project_dir.mkdir(parents=True)
        (project_dir / "pom.xml").write_text(
            _POM.replace("CLASSPATH_DEPENDENCIES\n", _classpath_dependencies(self.classpath))
        )

        main_src = project_dir / "src" / "main" / "java"
        main_src.mkdir(parents=True)

        mockito_ext = (
            project_dir / "src" / "main" / "resources"
            / "mockito-extensions"
        )
        mockito_ext.mkdir(parents=True)
        (mockito_ext / "org.mockito.plugins.MockMaker").write_text(
            # Real classpath: real code uses final classes (value objects, records,
            # Fineract's JsonCommand) that only the inline mock maker can fake. It
            # self-attaches an agent at startup — verified to work in the sandbox JRE.
            "mock-maker-inline\n"
        )

        # The project's compiled consumer is used as built (Lombok and other
        # annotation processors applied); otherwise compile a copy of its source.
        pkg = _read_package(consumer_file) or ""
        fqn_path = "/".join([*pkg.split("."), consumer_file.stem]) + ".class" if pkg else consumer_file.stem + ".class"
        if _classpath_contains(self.classpath, fqn_path):
            return
        pkg_dir = main_src / Path(*pkg.split(".")) if pkg else main_src
        pkg_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(consumer_file, pkg_dir / consumer_file.name)

    def _write_execution(
        self,
        project_dir: Path,
        analysis: ConsumerAnalysis,
        consumer_file: Path,
        test_config: "TestConfig | None",
        scenario_type: str = "UNKNOWN",
    ) -> None:
        exec_dir = (
            project_dir
            / "src" / "main" / "java"
            / "eventbreaker" / "generated"
        )
        exec_dir.mkdir(parents=True, exist_ok=True)
        code = _generate_execution(
            analysis, consumer_file, test_config, scenario_type,
            available_classes=self._classes,
        )
        (exec_dir / "EventBreakerExecution.java").write_text(code)

    # ── Build + run ───────────────────────────────────────────────────────────

    def _run(
        self,
        project_dir: Path,
        scenario: ChaosScenario,
        analysis: ConsumerAnalysis,
        consumer_file: Path,
        cached: bool = False,
    ) -> ObservationResult:

        # Step 1: compile + package into a self-contained fat JAR.
        # Skipped when a cached JAR already exists for this consumer.
        if not cached:
            build = subprocess.run(
                ["mvn", "package", "--no-transfer-progress", "-DskipTests", "-e"],
                capture_output=True,
                text=True,
                cwd=project_dir,
            )
            if build.returncode != 0:
                output = build.stdout + "\n" + build.stderr
                return ObservationResult(
                    scenario=scenario.scenarioType,
                    status="ERROR",
                    rawOutput=output,
                    executionError=_extract_build_error(output),
                )

        # Step 2: run the fat JAR — in Nebius Sandbox if credentials available,
        # otherwise fall back to local java.
        jar = project_dir / "target" / "eventbreaker-execution.jar"

        if os.environ.get("NEBIUS_PROJECT_ID"):
            try:
                # One self-contained JAR: the fat JAR plus only the classpath
                # classes it can reach (uploading every dependency jar times out).
                pkg = _read_package(consumer_file)
                consumer_fqn = f"{pkg}.{analysis.className}" if pkg else analysis.className
                upload = _sandbox_jar(jar, self.classpath, (consumer_fqn,))
                stdout, stderr = SandboxExecutor().execute(upload)
                output = stdout + "\n" + stderr
            except (RuntimeError, EnvironmentError) as e:
                return ObservationResult(
                    scenario=scenario.scenarioType,
                    status="ERROR",
                    executionError=f"Sandbox execution failed: {e}",
                )
        else:
            run = subprocess.run(
                ["java", "-cp", os.pathsep.join([str(jar), *map(str, self.classpath)]),
                 "eventbreaker.generated.EventBreakerExecution"],
                capture_output=True,
                text=True,
                cwd=project_dir,
            )
            output = run.stdout + "\n" + run.stderr

        return _parse_output(output, scenario)


# ── Code generator ────────────────────────────────────────────────────────────

def _generate_execution(
    analysis: ConsumerAnalysis,
    consumer_file: Path,
    test_config: "TestConfig | None",
    scenario_type: str = "UNKNOWN",
    available_classes: "set[str] | None" = None,
) -> str:
    """Generate the harness main class.

    The consumer compiles against its project's real classpath. Recorder fakes
    return fakes by default (no return-type guessing), the consumer is built
    through its real constructor by reflection, and the recorders are injected
    into its dependency fields afterwards.
    """
    consumer_pkg = _read_package(consumer_file) or ""

    # Start with whatever the consumer itself imports, minus imports only the
    # compiled consumer needed (Lombok and other compile-only annotations are
    # not on the runtime classpath).
    consumer_imports = _read_imports(consumer_file)
    if available_classes is not None:
        consumer_imports = [
            imp for imp in consumer_imports if _import_available(imp, available_classes)
        ]

    # The generated class lives in eventbreaker.generated — it cannot see the
    # consumer's package implicitly. Any type that the consumer uses without an
    # explicit import (i.e. same-package types) must be imported explicitly here.
    already_imported = {
        imp.split()[-1].rstrip(";").rsplit(".", 1)[-1]
        for imp in consumer_imports
        if imp.startswith("import ")
    }
    pkg_types: set[str] = set()
    pkg_types.add(analysis.eventType)
    for dep in analysis.dependencies:
        pkg_types.add(dep.type.split("<")[0].strip())
    for vi in analysis.variableInitializations:
        if vi.variableType:
            pkg_types.add(vi.variableType.split("<")[0].strip())
    for oc in analysis.objectCreations:
        if oc.type:
            pkg_types.add(oc.type.split("<")[0].strip())

    _skip = {
        "void", "boolean", "int", "long", "double", "float", "byte", "char",
        "String", "Integer", "Long", "Double", "Float", "Boolean", "Object",
        "List", "Map", "Set", "Collection", "Optional",
        "Exception", "RuntimeException",
    }
    extra_pkg_imports = [
        f"import {consumer_pkg}.{t};"
        for t in sorted(pkg_types)
        if t and t not in already_imported and t not in _skip and consumer_pkg
    ]

    extra_imports = "\n".join(consumer_imports + extra_pkg_imports)

    # Listener parameters other than the event (e.g. Kafka's Acknowledgment)
    # get recorder fakes too, so calls on them are observed.
    extra_params, call_args = _listener_arguments(analysis)
    recorders = list(analysis.dependencies) + [p for p in extra_params if not _is_value_param(p)]

    mock_decl_lines = ["        Map<String, Integer> threwCounts = new LinkedHashMap<>();"]
    value_param_lines = [
        f"        {p.type.strip()} {p.name} = ({_BOXED.get(p.type.strip(), p.type.strip())}) "
        f'eventbreaker.generated.__EBInstantiate.param("{p.name}", {p.type.strip()}.class);'
        for p in extra_params if _is_value_param(p)
    ]
    for d in recorders:
        dtype = d.type.split("<")[0].strip()
        mock_decl_lines.append(
            f"        {d.type} {d.name} = ({d.type}) mock(eventbreaker.generated.__EBInstantiate.typeFor({dtype}.class, \"{d.name}\"), "
            f'eventbreaker.generated.__EBInstantiate.settings({dtype}.class, "{d.name}")\n'
            f'            .invocationListeners(report -> {{\n'
            f'                String __ebMn = ((org.mockito.invocation.Invocation) report.getInvocation()).getMethod().getName();\n'
            f'                callSequence.add("{d.name}." + __ebMn);\n'
            f'                if (report.threwException()) threwCounts.merge("{d.name}." + __ebMn, 1, Integer::sum);\n'
            f"            }}));"
        )
    mock_declarations = "\n".join(mock_decl_lines)

    dep_map_entries = "\n".join(
        f'        __ebDeps.put("{d.name}", {d.name});'
        for d in recorders
    )

    dep_names_csv = ", ".join(d.name for d in recorders)
    reset_invocations = (
        f"        threwCounts.clear();\n"
        f"        callSequence.clear();\n"
        f"        clearInvocations({dep_names_csv});"
        if recorders else
        "        threwCounts.clear();\n        callSequence.clear();"
    )

    ctor = constructor_params(consumer_file.read_text(errors="replace"), analysis.className)
    ctor_names = "".join(f', "{name}"' for _, name in ctor)
    # Object + Class.forName: the consumer class may be package-private.
    fqn = f"{consumer_pkg}.{analysis.className}" if consumer_pkg else analysis.className
    lines = [
        f"        Object consumer = "
        f'eventbreaker.generated.__EBInstantiate.create(Class.forName("{fqn}"){ctor_names});'
    ]
    lines += [
        f'        eventbreaker.generated.__EBInstantiate.inject(consumer, "{d.name}", {d.name});'
        for d in analysis.dependencies
    ]
    consumer_instantiation = "\n".join(lines)

    fault_injections = _build_named_faults(test_config, consumer_file, available_classes) if test_config else ""
    call_pattern = _build_call_pattern(analysis, test_config, call_args) if test_config else (
        f"        try {{\n"
        f"            {_consumer_call(analysis, call_args)};\n"
        f"        }} catch (Exception __eb_ex) {{\n"
        f"            consumerThrew.set(true);\n            System.err.println(\"EB_CONSUMER_EXCEPTION \" + __eb_ex);\n"
        f"        }}"
    )

    return (
        _EXECUTION_TEMPLATE
        .replace("CAST_TYPES", "\n".join(
            f'        eventbreaker.generated.__EBInstantiate.CASTS.put("{root}", java.util.List.of('
            + ", ".join(f'"{t}"' for t in types) + "));"
            for root, types in cast_targets(analysis, consumer_file).items()
        ))
        .replace("EXTRA_IMPORTS", extra_imports)
        .replace("MOCK_DECLARATIONS", mock_declarations)
        .replace("CONSUMER_INSTANTIATION", consumer_instantiation)
        .replace("EVENT_CLASS", analysis.eventType)
        .replace("DEP_MAP_ENTRIES", dep_map_entries)
        .replace("RESET_INVOCATIONS", reset_invocations)
        .replace("FAULT_INJECTIONS", fault_injections)
        .replace("VALUE_PARAMS", "\n".join(value_param_lines))
        .replace("STATIC_HELPERS", "\n".join(
            f'        eventbreaker.generated.__EBInstantiate.STATICS.add("{fqn}");'
            for fqn in static_helpers(consumer_file.read_text(errors="replace"))
            if available_classes is None or fqn.replace(".", "/") in available_classes
        ))
        .replace("CALL_PATTERN", call_pattern)
        .replace("SCENARIO_TYPE", scenario_type)
    )


# ── TestConfig → Java builders ────────────────────────────────────────────────

# Listener parameters of these types get a value, not a fake (Mockito cannot fake them).
_VALUE_PARAM_TYPES = {
    "String", "CharSequence", "UUID", "BigDecimal", "boolean", "Boolean", "int", "Integer", "long", "Long",
    "double", "Double", "java.lang.String", "java.util.UUID", "java.math.BigDecimal",
}
_BOXED = {"boolean": "Boolean", "int": "Integer", "long": "Long", "double": "Double"}


_CAST = re.compile(r"\(\s*([A-Z][\w.]*)\s*\)\s*(?:this\s*\.\s*)?([a-z_]\w*)\b(?!\s*[.(\[])")


def cast_targets(analysis: ConsumerAnalysis, consumer_file: Path) -> dict[str, list[str]]:
    """Fake roots the listener casts to a more specific type: {"event": [fqn, …], "<dep>": […]}.

    `(InvoiceCreationInternalEvent) event` or `((InvoicePaymentControlPluginApi) plugin).call()`: the
    fake for that root must also be of that type, or the cast fails before the risky call.
    Only names cast directly (not a call result such as `(X) a.getB()`) are collected; the Java
    side keeps only loadable subtypes of the declared type.
    """
    source = consumer_file.read_text(encoding="utf-8")
    code = re.sub(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/', " ", source, flags=re.S)
    event_name = next(
        (p.split()[-1] for p in analysis.parameters if analysis.eventType in p.split()[:-1]), "event")
    roots = {d.name: d.name for d in analysis.dependencies}
    roots.update({name: name for _, name in constructor_params(source, analysis.className)})
    roots[event_name] = "event"
    found: dict[str, list[str]] = {}
    for type_name, name in _CAST.findall(code):
        root = roots.get(name)
        fqn = _qualify(type_name, consumer_file)
        if root and fqn not in found.setdefault(root, []):
            found[root].append(fqn)
    return {r: t for r, t in found.items() if t}


def constructor_params(source: str, class_name: str) -> list[tuple[str, str]]:
    """(type, name) of the class's constructor with the most parameters, from the source.

    The harness calls that same constructor; its fakes are named after these parameters.
    """
    best: list[tuple[str, str]] = []
    for m in re.finditer(rf"(?<![\w.]){re.escape(class_name)}\s*\(", source):
        # Skip "new ClassName(" and method calls; a constructor is preceded by a modifier, annotation or brace.
        before = source[:m.start()].rstrip()
        if before.endswith(("new", ".")) or not re.search(r"(public|protected|private|[;{}]|\))$", before):
            continue
        depth, i = 1, m.end()
        while i < len(source) and depth:
            depth += {"(": 1, ")": -1}.get(source[i], 0)
            i += 1
        params = _split_params(source[m.end():i - 1])
        if len(params) > len(best):
            best = params
    return best


def _split_params(text: str) -> list[tuple[str, str]]:
    parts, depth, cur = [], 0, ""
    for ch in text:
        depth += {"(": 1, "<": 1, ")": -1, ">": -1}.get(ch, 0)
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    out = []
    for raw in parts:
        cleaned = re.sub(r"@[\w.]+(\([^)]*\))?", " ", raw).replace("final ", " ").split()
        if len(cleaned) >= 2:
            out.append((" ".join(cleaned[:-1]), cleaned[-1]))
    return out


def static_helpers(source: str) -> list[str]:
    """Project classes whose static methods the listener's class calls ("org.acme.ctx.Tenant.current()").

    Project = same first three package segments as the listener (org.eclipse.hawkbit…); library and
    JDK statics stay untouched. Found via static imports and imported classes used as `Name.method(`.
    """
    pkg = re.search(r"^\s*package\s+([\w.]+)\s*;", source, re.M)
    if not pkg:
        return []
    root = ".".join(pkg.group(1).split(".")[:3]) + "."
    code = re.sub(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/', " ", source, flags=re.S)
    found: list[str] = []
    for cls in re.findall(r"^\s*import\s+static\s+([\w.]+)\.\w+\s*;", source, re.M):
        if cls.startswith(root):
            found.append(cls)
    for fqn in re.findall(r"^\s*import\s+([\w.]+)\s*;", source, re.M):
        simple = fqn.rsplit(".", 1)[-1]
        called = set(re.findall(rf"(?<![\w.]){simple}\.([a-z]\w*)\s*\(", code))
        # Only valueOf()/values(): an enum's built-ins, which the harness never fakes (MessageType.valueOf(type)).
        if fqn.startswith(root) and called - {"valueOf", "values"}:
            found.append(fqn)
    return list(dict.fromkeys(found))


def inherited_source(consumer_file: Path, max_chars: int = 12000) -> str:
    """Source of the listener's parent classes from the same project (closest first), or "".

    Shared logic often lives in a base class (reading headers, converting the message body); path
    setup cannot plan values for code it cannot see. Looked up in the listener's own source root,
    then in any src/main/java of the project. Stops at a parent outside the project.
    """
    root = project_root(consumer_file)
    current, parts, seen = consumer_file, [], set()
    while len(parts) < 3:
        source = current.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"\bclass\s+\w+(?:\s*<[^{]*?>)?\s+extends\s+([\w.]+)", source)
        if not m:
            break
        rel = _qualify(m.group(1), current).replace(".", "/") + ".java"
        pkg = _read_package(current) or ""
        src_root = current.parents[len(pkg.split(".")) if pkg else 0]
        found = src_root / rel if (src_root / rel).exists() else next(
            (p for p in (root.glob(f"**/src/main/java/{rel}") if root else []) if "/build/" not in str(p)), None)
        if found is None or found in seen:
            break
        seen.add(found)
        parts.append(f"// {found.name}\n{found.read_text(encoding='utf-8', errors='replace')}")
        current = found
    return "\n\n".join(parts)[:max_chars]


def value_params(analysis: ConsumerAnalysis) -> list[DependencyInfo]:
    """Plain-value listener parameters (e.g. hawkbit's @Header String type), settable as "param.<name>"."""
    return [p for p in _listener_arguments(analysis)[0] if _is_value_param(p)]


def _is_value_param(p: DependencyInfo) -> bool:
    return p.type.strip() in _VALUE_PARAM_TYPES


def _listener_arguments(analysis: ConsumerAnalysis) -> tuple[list[DependencyInfo], str]:
    """Non-event listener parameters, and the call's argument list.

    "(@Payload ContextualMessage msg, Acknowledgment ack)" → ([ack: Acknowledgment], "event, ack").
    The event goes to the parameter whose type is the event type; every other
    parameter is named after itself (prefixed if it clashes with a field). Plain values
    (_is_value_param) become values, everything else a recorder fake.
    """
    taken = {d.name for d in analysis.dependencies} | {"event", "consumer"}
    extra: list[DependencyInfo] = []
    args: list[str] = []
    event_used = False
    for raw in analysis.parameters:
        # Drop annotations (with their arguments) and modifiers; keep "Type name".
        cleaned = re.sub(r"@[\w.]+(\([^)]*\))?", " ", raw).replace("final ", " ").split()
        if len(cleaned) < 2:
            continue
        ptype, pname = " ".join(cleaned[:-1]), cleaned[-1]
        if not event_used and ptype.split("<")[0].strip() == analysis.eventType.split("<")[0].strip():
            args.append("event")
            event_used = True
            continue
        name = pname if pname not in taken else f"param_{pname}"
        taken.add(name)
        extra.append(DependencyInfo(name=name, type=ptype))
        args.append(name)
    if not event_used:  # e.g. ConsumerRecord<K, V>: not yet supported, fall back
        return [], "event"
    return extra, ", ".join(args)


def _build_named_faults(
    test_config: TestConfig, consumer_file: Path, available_classes: "set[str] | None" = None,
) -> str:
    """Real-classpath mode: register faults and return values by path on __EBInstantiate.

    Applied inside each fake's default answer, so no argument matchers are needed
    and calls made from private helpers are covered too.
    """
    lines: list[str] = []
    for fi in test_config.faultInjections:
        spec = f"DELAY:{fi.delayMs or 500}" if fi.fault == "DELAY" else fi.fault
        lines.append(
            f'        eventbreaker.generated.__EBInstantiate.FAULTS.put("{fi.dep}.{fi.method}", "{spec}");'
        )
    for rv in test_config.returnValues:
        target = rv.target.replace("()", "").replace("\\", "").replace('"', "")
        if rv.returns.startswith("MOCK:"):
            # Class literal, resolved through the consumer's imports/package. A class that
            # isn't on the classpath would break the build: skip that override instead.
            fqn = _qualify(rv.returns[5:].strip(), consumer_file)
            jdk = fqn.startswith(("java.", "jdk.")) or fqn.startswith(_JDK_JAVAX)
            if available_classes is not None and not jdk and fqn.replace(".", "/") not in available_classes:
                continue
            value = f"{fqn}.class"
        else:
            value = '"' + rv.returns.replace("\\", "\\\\").replace('"', '\\"') + '"'
        lines.append(
            f'        eventbreaker.generated.__EBInstantiate.RETURNS.put("{target}", {value});'
        )
    return "\n".join(lines)


def _qualify(type_name: str, consumer_file: Path) -> str:
    """Fully-qualified name for a simple type name used by the consumer (imports, then its package)."""
    if "." in type_name:
        return type_name
    for imp in _read_imports(consumer_file):
        m = re.match(r"import\s+([\w.]+)\.(\w+)\s*;", imp)
        if m and m.group(2) == type_name:
            return f"{m.group(1)}.{type_name}"
    pkg = _read_package(consumer_file)
    return f"{pkg}.{type_name}" if pkg else type_name


def _consumer_call(analysis: ConsumerAnalysis, call_args: str) -> str:
    """The Java expression that invokes the listener method once (reflectively: it may be package-private)."""
    return (f'eventbreaker.generated.__EBInstantiate.call(consumer, "{analysis.methodName}", '
            f"{call_args})")


def _build_call_pattern(
    analysis: ConsumerAnalysis, test_config: TestConfig, call_args: str = "event",
) -> str:
    """Build the consumer invocation block matching the requested callPattern."""
    call = _consumer_call(analysis, call_args)
    if test_config.callPattern == "SINGLE":
        return (
            f"        try {{\n"
            f"            {call};\n"
            f"        }} catch (Exception __eb_ex) {{\n"
            f"            consumerThrew.set(true);\n            System.err.println(\"EB_CONSUMER_EXCEPTION \" + __eb_ex);\n"
            f"        }}"
        )
    if test_config.callPattern == "DUPLICATE":
        return (
            f"        try {{ {call}; }} catch (Exception __eb_ex) {{ consumerThrew.set(true); System.err.println(\"EB_CONSUMER_EXCEPTION \" + __eb_ex); }}\n"
            f"        try {{ {call}; }} catch (Exception __eb_ex) {{ consumerThrew.set(true); System.err.println(\"EB_CONSUMER_EXCEPTION \" + __eb_ex); }}"
        )
    if test_config.callPattern == "CONCURRENT":
        return (
            f"        Thread __eb_t1 = new Thread(() -> {{\n"
            f"            try {{ {call}; }} catch (Exception __eb_ex) {{ consumerThrew.set(true); System.err.println(\"EB_CONSUMER_EXCEPTION \" + __eb_ex); }}\n"
            f"        }});\n"
            f"        Thread __eb_t2 = new Thread(() -> {{\n"
            f"            try {{ {call}; }} catch (Exception __eb_ex) {{ consumerThrew.set(true); System.err.println(\"EB_CONSUMER_EXCEPTION \" + __eb_ex); }}\n"
            f"        }});\n"
            f"        __eb_t1.start(); __eb_t2.start();\n"
            f"        __eb_t1.join(); __eb_t2.join();"
        )
    # fallback — should not happen (feasibility check guards against unknown patterns)
    return f"        {call};"


# ── Output parser ─────────────────────────────────────────────────────────────

def _parse_output(output: str, scenario: ChaosScenario) -> ObservationResult:
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("EVENTBREAKER_RESULT:"):
            data = json.loads(stripped[len("EVENTBREAKER_RESULT:"):])
            entries = [
                ObservationEntry(
                    target=o["target"],
                    callCount=o["callCount"],
                    threw=o.get("threw", False),
                )
                for o in data.get("observations", [])
            ]
            return ObservationResult(
                scenario=scenario.scenarioType,
                status="REPRODUCED",
                observations=entries,
                rawOutput=output,
                consumerThrew=data.get("consumerThrew", False),
                callSequence=data.get("callSequence", []),
                consumerException=next(
                    (l.strip()[len("EB_CONSUMER_EXCEPTION "):] for l in output.splitlines()
                     if l.strip().startswith("EB_CONSUMER_EXCEPTION ")),
                    None,
                ),
            )

    # The harness reports its own crash (before the listener ran, or while collecting results).
    crash = next((l.strip()[len("EVENTBREAKER_ERROR:"):] for l in output.splitlines()
                  if l.strip().startswith("EVENTBREAKER_ERROR:")), None)
    return ObservationResult(
        scenario=scenario.scenarioType,
        status="ERROR",
        observations=[],
        rawOutput=output,
        executionError=(
            f"The harness crashed: {crash}" if crash else
            "No EVENTBREAKER_RESULT found. "
            "The build or execution may have failed — check rawOutput."
        ),
    )



# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_package(java_file: Path) -> str | None:
    for line in java_file.read_text(errors="replace").splitlines()[:20]:
        m = re.match(r"^\s*package\s+([\w.]+)\s*;", line)
        if m:
            return m.group(1)
    return None


def _extract_build_error(output: str) -> str:
    """Pull the most useful lines from Maven output for user-facing display."""
    error_lines = []
    missing_symbols: list[str] = []

    for line in output.splitlines():
        stripped = line.strip()
        # Java compiler errors: "[ERROR] /file.java:[7,19] ..." or "error: cannot find symbol"
        if stripped.lower().startswith("[error]") or "error:" in stripped.lower():
            error_lines.append(stripped)
        # Capture "symbol: class Foo" lines that follow cannot-find-symbol
        if stripped.startswith("symbol:") or stripped.startswith("location:"):
            error_lines.append(stripped)
        if "cannot find symbol" in stripped.lower():
            missing_symbols.append(stripped)

    if missing_symbols:
        unique = list(dict.fromkeys(error_lines))[:10]
        detail = "\n".join(unique)
        return (
            "Build failed: the consumer references types that EventBreaker "
            "could not find on the classpath.\n\n"
            "Tip: build the listener's module first (mvn compile / ./gradlew classes), "
            "or pass the missing jars with --classpath.\n\n"
            f"Compiler errors:\n{detail}"
        )

    if error_lines:
        return "Build failed:\n" + "\n".join(error_lines[:10])

    # Fallback: last non-empty lines of output
    last = [l for l in output.splitlines() if l.strip()][-8:]
    return "Build failed:\n" + "\n".join(last)


def _jar_exists(project_dir: Path) -> bool:
    return (project_dir / "target" / "eventbreaker-execution.jar").exists()


def _read_imports(java_file: Path) -> list[str]:
    imports = []
    for line in java_file.read_text(errors="replace").splitlines():
        stripped = line.strip()
        if stripped.startswith("import "):
            imports.append(stripped)
        elif re.match(r"^(public\s+)?(class|interface|enum|record)\s+", stripped):
            break
    return imports
