package dsarp.rewrite;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonProperty;
import org.openrewrite.ExecutionContext;
import org.openrewrite.Option;
import org.openrewrite.Recipe;
import org.openrewrite.ScanningRecipe;
import org.openrewrite.TreeVisitor;
import org.openrewrite.java.JavaIsoVisitor;
import org.openrewrite.java.MethodMatcher;
import org.openrewrite.java.tree.J;
import org.openrewrite.java.tree.JavaType;
import org.openrewrite.java.tree.Statement;
import org.openrewrite.java.tree.TypeUtils;

import java.util.ArrayList;
import java.util.List;

/**
 * Moves an exact public static method between existing classes and rewrites
 * statically attributed call sites. It deliberately refuses general instance
 * methods; candidate resolution must prove the narrower preconditions first.
 */
public class MoveMethod extends ScanningRecipe<MoveMethod.Accumulator> {
    static final class Accumulator {
        J.MethodDeclaration method;
        boolean destinationConflict;
    }
    @Option(displayName = "Source class", description = "Fully qualified declaring class", example = "org.example.A")
    private final String sourceClass;
    @Option(displayName = "Method pattern", description = "Exact method name and parameter signature", example = "helper(org.example.B)")
    private final String methodPattern;
    @Option(displayName = "Target class", description = "Existing fully qualified target class", example = "org.example.B")
    private final String targetClass;

    @JsonCreator
    public MoveMethod(@JsonProperty("sourceClass") String sourceClass,
                      @JsonProperty("methodPattern") String methodPattern,
                      @JsonProperty("targetClass") String targetClass) {
        this.sourceClass = sourceClass; this.methodPattern = methodPattern; this.targetClass = targetClass;
    }

    @Override public String getDisplayName() { return "Move a conservatively resolved static method"; }
    @Override public String getDescription() { return "Moves one exact public static method to an existing type and updates attributed static calls."; }
    @Override public Accumulator getInitialValue(ExecutionContext ctx) { return new Accumulator(); }

    private MethodMatcher matcher() { return new MethodMatcher(sourceClass + " " + methodPattern, true); }

    @Override public TreeVisitor<?, ExecutionContext> getScanner(Accumulator acc) {
        return new JavaIsoVisitor<>() {
            @Override public J.MethodDeclaration visitMethodDeclaration(J.MethodDeclaration method, ExecutionContext ctx) {
                J.MethodDeclaration m = super.visitMethodDeclaration(method, ctx);
                JavaType.Method mt = m.getMethodType();
                if (mt != null && matcher().matches(mt)
                        && mt.getFlags().contains(org.openrewrite.java.tree.Flag.Public)
                        && mt.getFlags().contains(org.openrewrite.java.tree.Flag.Static)
                        && m.getBody() != null) {
                    if (acc.method == null) acc.method = m;
                }
                if (mt != null && new MethodMatcher(targetClass + " " + methodPattern, true).matches(mt))
                    acc.destinationConflict = true;
                return m;
            }
        };
    }

    @Override public TreeVisitor<?, ExecutionContext> getVisitor(Accumulator acc) {
        return new JavaIsoVisitor<>() {
            @Override public J.ClassDeclaration visitClassDeclaration(J.ClassDeclaration type, ExecutionContext ctx) {
                J.ClassDeclaration cd = super.visitClassDeclaration(type, ctx);
                JavaType.FullyQualified fq = TypeUtils.asFullyQualified(cd.getType());
                if (fq == null || acc.method == null || acc.destinationConflict) return cd;
                if (sourceClass.equals(fq.getFullyQualifiedName())) {
                    List<Statement> statements = new ArrayList<>();
                    for (Statement statement : cd.getBody().getStatements()) {
                        if (!(statement instanceof J.MethodDeclaration method) || method.getMethodType() == null
                                || !matcher().matches(method.getMethodType())) statements.add(statement);
                    }
                    return cd.withBody(cd.getBody().withStatements(statements));
                }
                if (targetClass.equals(fq.getFullyQualifiedName())) {
                    for (Statement statement : cd.getBody().getStatements()) {
                        if (statement instanceof J.MethodDeclaration method && method.getMethodType() != null
                                && method.getSimpleName().equals(acc.method.getSimpleName())
                                && method.getParameters().size() == acc.method.getParameters().size()) return cd;
                    }
                    List<Statement> statements = new ArrayList<>(cd.getBody().getStatements());
                    statements.add(acc.method.withPrefix(org.openrewrite.java.tree.Space.format("\n\n")));
                    return cd.withBody(cd.getBody().withStatements(statements));
                }
                return cd;
            }

            @Override public J.MethodInvocation visitMethodInvocation(J.MethodInvocation invocation, ExecutionContext ctx) {
                J.MethodInvocation mi = super.visitMethodInvocation(invocation, ctx);
                JavaType.Method mt = mi.getMethodType();
                if (acc.destinationConflict || mt == null || !matcher().matches(mt) || !(mi.getSelect() instanceof J.Identifier id)) return mi;
                JavaType.FullyQualified target = JavaType.ShallowClass.build(targetClass);
                maybeRemoveImport(sourceClass); maybeAddImport(targetClass);
                return mi.withSelect(id.withSimpleName(targetClass.substring(targetClass.lastIndexOf('.') + 1)).withType(target))
                        .withMethodType(mt.withDeclaringType(target));
            }
        };
    }
}
