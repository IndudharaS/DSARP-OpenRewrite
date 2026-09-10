package dsarp.rewrite;

import org.junit.jupiter.api.Test;
import org.openrewrite.test.RewriteTest;

import static org.openrewrite.java.Assertions.java;

class MoveMethodTest implements RewriteTest {
    @Test void movesExactPublicStaticMethodAndUpdatesCall() {
        rewriteRun(
                spec -> spec.recipe(new MoveMethod("example.A", "helper(example.B)", "example.B")),
                java(
                        """
                        package example;
                        public class A {
                            public static void helper(B b) { b.run(); }
                            public void invoke(B b) { A.helper(b); }
                        }
                        """,
                        """
                        package example;
                        public class A {
                            public void invoke(B b) { B.helper(b); }
                        }
                        """
                ),
                java(
                        """
                        package example;
                        public class B {
                            public void run() {}
                        }
                        """,
                        """
                        package example;
                        public class B {
                            public void run() {}

                            public static void helper(B b) { b.run(); }
                        }
                        """
                )
        );
    }

    @Test void destinationConflictLeavesSourcesUnchanged() {
        rewriteRun(
                spec -> spec.recipe(new MoveMethod("example.A", "helper(example.B)", "example.B")),
                java("package example; public class A { public static void helper(B b) {} }"),
                java("package example; public class B { public static void helper(B b) {} }")
        );
    }
}
